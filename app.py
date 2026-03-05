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
    if not WFP_SECRET_KEY:
        return True
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
    return expected == data.get("merchantSignature", "")

def normalize_phone(phone):
    """Повертає останні 9 цифр номера для порівняння"""
    if not phone:
        return None
    digits = "".join(c for c in phone if c.isdigit())
    return digits[-9:] if len(digits) >= 9 else digits

def find_order(email=None, phone=None):
    # Спочатку шукаємо по email
    if email:
        resp = requests.get(f"{KEYCRM_BASE}/order", headers=HEADERS,
                            params={"filter[buyer_email]": email, "limit": 1, "sort[id]": "desc"})
        orders = resp.json().get("data", [])
        if orders:
            return orders[0]

    # Якщо не знайшли — шукаємо по телефону (останні 9 цифр)
    if phone:
        phone_suffix = normalize_phone(phone)
        # Пробуємо різні формати
        for phone_variant in [phone, f"+38{phone_suffix}", f"+48{phone_suffix}", phone_suffix]:
            resp = requests.get(f"{KEYCRM_BASE}/order", headers=HEADERS,
                                params={"filter[buyer_phone]": phone_variant, "limit": 5, "sort[id]": "desc"})
            orders = resp.json().get("data", [])
            # Порівнюємо по останніх 9 цифрах
            for o in orders:
                buyer_phone = (o.get("buyer") or {}).get("phone", "")
                if normalize_phone(buyer_phone) == phone_suffix:
                    return o

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

    email = data.get("clientEmail")
    phone = data.get("clientPhone")
    amount = data.get("amount", 19)
    reference = data.get("orderReference", "")

    order = find_order(email=email, phone=phone)

    if order:
        result = add_payment(order["id"], amount, reference)
        logging.info(f"Payment added to order {order['id']}: {result}")
    else:
        new_order = create_order(data)
        order_id = new_order.get("id")
        if order_id:
            add_payment(order_id, amount, reference)
        logging.info(f"New order created: {new_order}")

    return wfp_response(reference, WFP_SECRET_KEY)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
