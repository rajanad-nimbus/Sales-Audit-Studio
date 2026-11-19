# ReSA workbook RTLOG fixtures

These synthetic, fixed-width v14 RTLOG files cover three business dates and four store days. They contain no real customer or payment data. Use USD when importing.

Create or verify Store foundation references before importing. Set store `9751` to area `2500` (BSI) and store `9752` to area `3500` (Outlet). Area-dependent totals use these values.

In ZeTSA, use **Imports → Upload → Oracle Retail RTLOG** to upload the files in the order below with a unique delivery ID per file. Retotal each store day and compare its configured totals with the checkpoints. Replay a file with a new delivery ID to check duplicate handling. The replay should create zero new canonical transactions and register events.

| File | Focus | Canonical transactions | Register events |
|---|---|---:|---:|
| [RTLOG_9751_20261117.DAT](RTLOG_9751_20261117.DAT) | Discounted sale, return, exchange, split tender and change, signed declarations, marketplace shipping and taxes, till activity and post-void | 8 | 11 |
| [RTLOG_9751_20261118.DAT](RTLOG_9751_20261118.DAT) | Client tender codes, personal and traveler checks, Mall Certificate routed through Cash, and 57 distinct workbook POS declaration references | 18 | 57 |
| [RTLOG_9751_20261119.DAT](RTLOG_9751_20261119.DAT) | BSI local and remote sales, non-merchandise items, gift-card, MCR, and virtual voucher ranges, and MCR issued on return | 40 | 0 |
| [RTLOG_9752_20261119.DAT](RTLOG_9752_20261119.DAT) | Outlet local and remote sales, non-merchandise items, area-restricted postage and marketplace shipping | 35 | 0 |

## Expected checkpoints

All amounts are USD.

- **9751, November 17.** `CASHTOTAL` 31.00, `CASHTOTALP` 26.00, `CASHTOTALO` 5.00, `VISAO` −1.00, `SLSCLBSI` 19.00. Amazon, eBay, and Walmart shipping are 3.00, 4.00, and 5.00. `SLSTAXACC` is 2.00, `SLSTXAMZN` is 1.00, and `SLSTXEBAY` is 1.00. The loan and pull remain register events.
- **9751, November 18.** `CASH` 22.00, `CHECK` 24.00, `CASHTOTAL` 46.00, `CASHTOTALP` 16.00, `CASHTOTALO` 30.00, `VISA` 15.00, `GIFTCRDU` 9.00.
- **9751, November 19.** `SLSCLBSI` 19.00, `REMSLSBSI` 12.00, `GFTCRDSL` 23.00, `VIRTGCSL` 13.00, `MCRISSUE` −4.00, `GIFTCRDU` −4.00, `GCSLTTL` 27.00, `MCRCASHED` 23.00, `CRTSYSTPRE` 1.00.
- **9752, November 19.** `SLSCLOUT` 17.00, `REMSLSOUT` 12.00, `POSTAGEXP` 1.00. Amazon, eBay, and Walmart shipping are 3.00, 4.00, and 5.00.

The November 18 file follows the currently configured POS references, including `CASHOVRP` reading `SALESCLP` and `SLSTAXACCP` reading `SLSTAXACCO`. Confirm those mappings against a real source feed before using them for acceptance. GL posting and location-trait membership require separate environment setup. NIU definitions are inactive.

## Clean store day

[RTLOG_9753_20261119.DAT](RTLOG_9753_20261119.DAT) contains one USD 20.00 cash sale, a matching cash declaration, and the day-close marker. Use Store `9753` with area `4500` and active item `QA-CLEAN-SHIRT`. This area is outside the workbook’s BSI and Outlet sales-clearing ranges.

Load the [supporting feeds](clean-supporting-feeds.json) with the RTLOG before reconciliation. They contain a matching POS control and ERP posting, plus a paid USD 10.00 Shopify order and matching Shopify day total. The approved store-day absence rule requires an Order record even on this POS store day.

Expected result after reconciliation and retotaling is **Complete** ingestion, day-close marker **Received**, audit **Clean**, and zero open cases, unexplained variances, or transaction-number gaps. POS control, over/short, processor-to-bank, and POS-to-GL variances are all USD 0.00. The normal auditor sign-off still applies.
