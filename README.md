# WayForPay → KeyCRM Webhook

## Змінні середовища (Environment Variables)

| Змінна | Опис | Обов'язкова |
|--------|------|-------------|
| `KEYCRM_API_KEY` | API ключ KeyCRM (2bk Agency) | ✅ |
| `WFP_SECRET_KEY` | Secret key з кабінету WayForPay | ✅ |
| `KEYCRM_SOURCE_ID` | ID джерела в KeyCRM (Tilda) | ✅ |

## Endpoint

`POST /webhook` — приймає webhook від WayForPay

## Логіка

1. WayForPay надсилає POST на `/webhook` після кожного платежу
2. Скрипт перевіряє підпис
3. Якщо статус = `Approved`:
   - Шукає замовлення в KeyCRM по email/телефону
   - Якщо знайдено → оновлює статус на "Оплачено"
   - Якщо не знайдено → створює нове замовлення
4. Повертає підтвердження для WayForPay
