"""Bookings CRUD. Items: pk=USER#<sub>, sk=BOOKING#<id>. Slot uniqueness via a SLOT# lock item."""
import json, os, uuid
from datetime import datetime, timezone
import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
client = boto3.client("dynamodb")


def resp(code, body):
    return {"statusCode": code,
            "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
            "body": json.dumps(body, default=str)}


def user_id(event):
    return event["requestContext"]["authorizer"]["claims"]["sub"]


def create(event):
    try:
        data = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return resp(400, {"error": "Invalid JSON"})
    missing = [f for f in ("doctor", "date", "time") if not data.get(f)]
    if missing:
        return resp(400, {"error": f"Missing fields: {', '.join(missing)}"})
    try:
        datetime.fromisoformat(f"{data['date']}T{data['time']}")
    except ValueError:
        return resp(400, {"error": "date must be YYYY-MM-DD and time HH:MM"})

    uid, bid = user_id(event), str(uuid.uuid4())
    slot = f"{data['doctor']}#{data['date']}T{data['time']}"
    item = {"pk": f"USER#{uid}", "sk": f"BOOKING#{bid}", "id": bid, "slot": slot,
            "doctor": data["doctor"], "date": data["date"], "time": data["time"],
            "reason": data.get("reason", ""), "status": "CONFIRMED",
            "createdAt": datetime.now(timezone.utc).isoformat()}
    ser = boto3.dynamodb.types.TypeSerializer()
    try:
        # Atomically write booking + slot lock so a slot can't be double-booked
        client.transact_write_items(TransactItems=[
            {"Put": {"TableName": table.name,
                     "Item": {k: ser.serialize(v) for k, v in item.items()}}},
            {"Put": {"TableName": table.name,
                     "Item": {"pk": {"S": f"SLOT#{slot}"}, "sk": {"S": "LOCK"}, "bookingId": {"S": bid}},
                     "ConditionExpression": "attribute_not_exists(pk)"}},
        ])
    except ClientError as e:
        if e.response["Error"]["Code"] == "TransactionCanceledException":
            return resp(409, {"error": "Slot already booked"})
        raise
    return resp(201, item)


def list_bookings(event):
    r = table.query(KeyConditionExpression=Key("pk").eq(f"USER#{user_id(event)}") & Key("sk").begins_with("BOOKING#"))
    return resp(200, r.get("Items", []))


def get_booking(event, bid):
    r = table.get_item(Key={"pk": f"USER#{user_id(event)}", "sk": f"BOOKING#{bid}"})
    return resp(200, r["Item"]) if "Item" in r else resp(404, {"error": "Not found"})


def cancel(event, bid):
    key = {"pk": f"USER#{user_id(event)}", "sk": f"BOOKING#{bid}"}
    r = table.get_item(Key=key)
    if "Item" not in r:
        return resp(404, {"error": "Not found"})
    client.transact_write_items(TransactItems=[
        {"Update": {"TableName": table.name, "Key": {"pk": {"S": key["pk"]}, "sk": {"S": key["sk"]}},
                    "UpdateExpression": "SET #s = :c", "ExpressionAttributeNames": {"#s": "status"},
                    "ExpressionAttributeValues": {":c": {"S": "CANCELLED"}}}},
        {"Delete": {"TableName": table.name,
                    "Key": {"pk": {"S": f"SLOT#{r['Item']['slot']}"}, "sk": {"S": "LOCK"}}}},
    ])
    return resp(204, {})


def handler(event, context):
    method, bid = event["httpMethod"], (event.get("pathParameters") or {}).get("id")
    try:
        if method == "POST":
            return create(event)
        if method == "GET":
            return get_booking(event, bid) if bid else list_bookings(event)
        if method == "DELETE" and bid:
            return cancel(event, bid)
        return resp(405, {"error": "Method not allowed"})
    except Exception as e:  # log and hide internals
        print(f"ERROR: {e!r}")
        return resp(500, {"error": "Internal error"})
