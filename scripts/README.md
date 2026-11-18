# Scripts

Operational scripts, including database seeding and supplier synchronization, will be added with their respective implementation milestones.
## Bootstrap and webhook scripts

`bootstrap_data.py` is run by the API container at startup after migrations. It
synchronizes DummyJSON and falls back to one deterministic product if the
supplier cannot be reached.

`send_test_webhook.py` sends a correctly HMAC-signed assignment event:

```sh
PAYMENT_WEBHOOK_SECRET=your-local-secret \
  python scripts/send_test_webhook.py HOLD_ID --event payment.succeeded
```
