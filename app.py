from flask import Flask, request, jsonify
import hashlib, hmac, requests, os, logging

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

KEYCRM_API_KEY = os.environ.get("KEYCRM_API_KEY")
WFP_SECRET_KEY = os.environ.get("WFP_SECRET_KEY")
KEYCRM_BASE = "https://openapi.keycrm.app/v1"

HEADERS = {
    "Authorization": f"Bearer {KEYCRM_API_KEY}",
    "Content-Type": "application/json"
}

def verify_wayforpay_signature(data):
    """Перевірка підпису WayForPay"""
    if not WFP_SECRET_KEY:
        return True  # Пропускаємо якщо ключ не вказаний (для тестів)
    
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

def find_order_in_keycrm(email=None, phone=None):
    """Шукаємо замовлення в KeyCRM по email або телефону"""
    if email:
        resp = requests.get(
            f"{KEYCRM_BASE}/order",
            headers=HEADERS,
            params={"filter[buyer_email]": email, "limit": 1, "sort[id]": "desc"}
        )
        data = resp.json()
        if data.get("data"):
            return data["data"][0]
    
    if phone:
        resp = requests.get(
            f"{KEYCRM_BASE}/order",
            headers=HEADERS,
            params={"filter[buyer_phone]": phone, "limit": 1, "sort[id]": "desc"}
        )
        data = resp.json()
        if data.get("data"):
            return data["data"][0]
    
    return None

def create_order_in_keycrm(wfp_data):
    """Створюємо нове замовлення в KeyCRM"""
    order = {
        "source_id": int(os.environ.get("KEYCRM_SOURCE_ID", 1)),
        "buyer": {
            "full_name": f"{wfp_data.get('clientFirstName', '')} {wfp_data.get('clientLastName', '')}".strip(),
            "email": wfp_data.get("clientEmail", ""),
            "phone": wfp_data.get("clientPhone", "")
        },
        "products": [{
            "name": wfp_data.get("productName", ["Woman Room підписка"])[0] if isinstance(wfp_data.get("productName"), list) else "Woman Room підписка",
            "price": float(wfp_data.get("amount", 19)),
            "quantity": 1
        }],
        "total_price": float(wfp_data.get("amount", 19)),
        "comment": f"WayForPay | {wfp_data.get('orderReference', '')}",
        "payment_status": "paid",
        "payment_method": "WayForPay"
    }
    
    resp = requests.post(f"{KEYCRM_BASE}/order", headers=HEADERS, json=order)
    return resp.json()

def update_order_payment(order_id, amount, reference):
    """Оновлюємо статус оплати існуючого замовлення"""
    resp = requests.patch(
        f"{KEYCRM_BASE}/order/{order_id}",
        headers=HEADERS,
        json={
            "payment_status": "paid",
            "comment": f"Рекурентний платіж WayForPay | {reference} | {amount} USD"
        }
    )
    return resp.json()

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "WayForPay → KeyCRM webhook"})

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.form.to_dict() if request.form else request.json or {}
    
    logging.info(f"Incoming webhook: {data}")
    
    # Перевірка підпису
    if not verify_wayforpay_signature(data):
        logging.warning("Invalid signature!")
        return jsonify({"status": "error", "message": "Invalid signature"}), 403
    
    transaction_status = data.get("transactionStatus", "")
    
    # Обробляємо тільки успішні платежі
    if transaction_status != "Approved":
        logging.info(f"Skipping status: {transaction_status}")
        return jsonify({"status": "ok", "message": f"Skipped: {transaction_status}"})
    
    email = data.get("clientEmail")
    phone = data.get("clientPhone")
    amount = data.get("amount")
    reference = data.get("orderReference", "")
    
    logging.info(f"Approved payment: {email}, {amount}")
    
    # Шукаємо існуюче замовлення
    existing_order = find_order_in_keycrm(email=email, phone=phone)
    
    if existing_order:
        # Оновлюємо статус оплати
        order_id = existing_order["id"]
        result = update_order_payment(order_id, amount, reference)
        logging.info(f"Updated order {order_id}: {result}")
        action = "updated"
    else:
        # Створюємо нове замовлення
        result = create_order_in_keycrm(data)
        logging.info(f"Created new order: {result}")
        action = "created"
    
    # WayForPay вимагає підтвердження
    return jsonify({
        "orderReference": reference,
        "status": "accept",
        "time": int(__import__("time").time()),
        "signature": hmac.new(
            (WFP_SECRET_KEY or "").encode(),
            f"{reference};accept;{int(__import__('time').time())}".encode(),
            hashlib.md5
        ).hexdigest() if WFP_SECRET_KEY else "test"
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
