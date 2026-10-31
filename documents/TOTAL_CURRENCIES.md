# Currency handling for store-day totals

Store-day calculations currently require one currency across all transactions and POS control manifests. They do not perform currency conversion. A mixed-currency day is rejected before a new totals version is created.

Source records may specify a three-letter uppercase `currency` code. When omitted, the existing default is USD, including for POS control manifests. A manifest for a non-USD store day must therefore include its currency. Empty store days retain USD as the default. Total previews check the currencies of the store day's transactions; the full retotal also checks control manifests.

Reconciliation cases preserve their currency, and action-center summaries separate amounts by currency. Older cases may have an unknown currency. A finance user can set an unknown case currency once using `PUT /api/cases/{id}` with `{"currency": "EUR"}` after verifying the source evidence. Financial adjustment exports reject cases with unknown currency; resolution exports carry a null currency until it is established.

The built-in autonomy and bulk financial thresholds are denominated in USD. The policy requires human review for non-USD or unknown-currency cases. Bulk approval does not apply its financial threshold to non-USD or unknown currencies, and financially impactful evidence overrides in those currencies require a second finance approver. No exchange rate is inferred.

Each configured monetary audit rule has a `threshold_currency` and applies only to store days in that currency. A null threshold currency is permitted for configured Count totals and creates no financial exposure. Existing rules default to USD. The reconciliation policy has one `amount_tolerance_currency`, also defaulting to USD; amounts in other currencies are compared exactly until a tolerance is configured for that currency. The current policy supports one tolerance currency at a time.

Approved missing-transaction corrections retain `ManualAdjustment` as their archived source and use the appropriate source mapping for totals and reconciliation. A refund must specify `source_system` as `Processor` or `Shopify`, since both accept that transaction type. Older approved manual records with the legacy `manual-adjustment-v1` mapping are not automatically reclassified.

Shopify matching requires a payment reference and uses the store and currency as part of the match. A manual Shopify order without a verified paid status remains unmatched; a refund without a reference produces an unmatched-refund case.
