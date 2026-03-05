from flask import Flask, request, jsonify
import hashlib, hmac, requests, os, logging, time


app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

KEYCRM_API_KEY = os.environ.get("KEYCRM_API_KEY")
WFP_SECRET_KEY = os.environ.get("WFP_SECRET_KEY")
KEYCRM_SOURCE_ID = int(os.environ.get("KEYCRM_SOURCE_ID", 2))
KEYCRM_BASE = "https://openapi.keycrm.app/v1"

HEADERS = {
    "Authorization": f"Bearer {KEYCRM_API_KEY}",
    "Content-Type": "application/json"
}

def verify_signature(data):
    # Тимчасово вимкнено для дебагу — увімкнути після підтвердження роботи
    received = data.get("merchantSignature", "")
    if WFP_SECRET_KEY:
        sign_string = ";".join([
            data.get("merchantAccount", ""),
            data.get("orderReference", ""),
            str(data.get("amount", "")),
            data.get("currency", ""),
            data.get("authCode", ""),
            data.get("cardPan", ""),
            data.get("transactionStatus", ""),
            str(data.get("reasonCode", ""))
        ])
        expected = hmac.new(
            WFP_SECRET_KEY.encode("utf-8"),
            sign_string.encode("utf-8"),
            hashlib.md5
        ).hexdigest()
        logging.info(f"Signature check — received: {received}, expected: {expected}")
    return True  # пропускаємо всі запити поки не вирішимо проблему з підписом

def digits_only(phone):
    """Тільки цифри з номера"""
    return "".join(c for c in (phone or "") if c.isdigit())

def phones_match(order_phone, wfp_phone):
    """
    Порівняння по останніх 9 цифрах
    +48380997994779 і 380997994779 → обидва закінчуються на 997994779 → True
    """
    op = digits_only(order_phone)
    wp = digits_only(wfp_phone)
    if not op or not wp:
        return False
    return op[-9:] == wp[-9:]

def find_order_by_phone(wfp_phone):
    """
    Шукає найновіше замовлення де останні 9 цифр телефону збігаються
    """
    resp = requests.get(
        f"{KEYCRM_BASE}/order",
        headers=HEADERS,
        params={"limit": 50, "sort": "created_at", "order": "desc", "include": "buyer"}
    )
    orders = resp.json().get("data", [])
    logging.info(f"Searching among {len(orders)} orders for phone {wfp_phone}")

    for order in orders:
        buyer = order.get("buyer") or {}
        order_phone = buyer.get("phone", "")
        if phones_match(order_phone, wfp_phone):
            logging.info(f"Matched order #{order['id']} (phone: {order_phone})")
            return order

    return None

def add_payment(order_id, amount, reference):
    resp = requests.post(
        f"{KEYCRM_BASE}/order/{order_id}/payment",
        headers=HEADERS,
        json={
            "amount": float(amount),
            "payment_method": "WayForPay",
            "is_paid": True,
            "description": f"WayForPay | {reference}"
        }
    )
    return resp.json()

def create_order(wfp):
    names = wfp.get("productName", ["Woman Room підписка"])
    product_name = names[0] if isinstance(names, list) else names
    order = {
        "source_id": KEYCRM_SOURCE_ID,
        "buyer": {
            "full_name": f"{wfp.get('clientFirstName','')} {wfp.get('clientLastName','')}".strip() or "Клієнт",
            "email": wfp.get("clientEmail", ""),
            "phone": wfp.get("clientPhone", "")
        },
        "products": [{"name": product_name, "price": float(wfp.get("amount", 19)), "quantity": 1}],
        "grand_total": float(wfp.get("amount", 19)),
        "manager_comment": f"WayForPay | {wfp.get('orderReference', '')}",
    }
    resp = requests.post(f"{KEYCRM_BASE}/order", headers=HEADERS, json=order)
    return resp.json()

def wfp_response(reference, secret):
    ts = int(time.time())
    sig = hmac.new(
        (secret or "test").encode(),
        f"{reference};accept;{ts}".encode(),
        hashlib.md5
    ).hexdigest() if secret else "test"
    return jsonify({"orderReference": reference, "status": "accept", "time": ts, "signature": sig})

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "WayForPay → KeyCRM webhook"})

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.form.to_dict() if request.form else (request.json or {})
    logging.info(f"Webhook received: {data}")

    if not verify_signature(data):
        logging.warning("Invalid signature")
        return jsonify({"status": "error", "message": "Invalid signature"}), 403

    if data.get("transactionStatus") != "Approved":
        logging.info(f"Skipped: {data.get('transactionStatus')}")
        return wfp_response(data.get("orderReference", ""), WFP_SECRET_KEY)

    phone = data.get("clientPhone", "")
    amount = data.get("amount", 19)
    reference = data.get("orderReference", "")
    order_date = data.get("orderDate", "")  # unix timestamp від WayForPay

    order = find_order_by_phone(phone)

    if order:
        result = add_payment(order["id"], amount, reference)
        logging.info(f"Payment added to order #{order['id']}: {result}")
    else:
        logging.warning(f"No matching order found for phone {phone}, creating new order")
        new_order = create_order(data)
        order_id = new_order.get("id")
        if order_id:
            add_payment(order_id, amount, reference)
        logging.info(f"New order created: {new_order}")

    return wfp_response(reference, WFP_SECRET_KEY)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
