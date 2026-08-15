LUXE RADAR V3.4.0 — MONETIZATION READY

- V3.3.0 shopping-first UI preserved.
- Free / Premium / Pro offer architecture.
- Premium: local Automatic Radar beta + price-alert positioning.
- Pro: reseller tools positioning.
- Billing remains OFF by default and requires explicit server configuration.
- Stripe lookup keys versioned (v340) so future price changes do not reuse stale Price objects.
- UI settings migration bug fixed: uiVersion now stays on 340 instead of being reset to 320.
- No .env included.

Important: Automatic Radar is currently a local saved watch + one-click rerun. Background notifications while the browser is closed require accounts, database, scheduler and notification delivery.
