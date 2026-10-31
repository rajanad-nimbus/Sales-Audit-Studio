# Export webhook signatures

Export webhooks include the existing `X-Nimbus-Signature`, an HMAC-SHA256 of the
raw request body. They also include `X-Nimbus-Envelope-Signature`, which binds
the body to `X-Nimbus-Dataset` and `X-Nimbus-Idempotency-Key`.

To verify the envelope signature, compute HMAC-SHA256 with the destination's
shared secret over the ASCII bytes of these four lines, separated by a single
line feed, with no trailing line feed:

1. `nimbus-export-v1`
2. The exact dataset header value.
3. The exact idempotency key header value.
4. The lowercase hexadecimal SHA-256 digest of the raw body.

The header value is `sha256=` followed by the lowercase hexadecimal HMAC.
Validate the dataset and key format, then compare signatures in constant time.
The built-in test receiver requires both signatures. Existing external receivers
can continue verifying the original body signature; they must adopt the envelope
signature to authenticate the dataset and idempotency key. Receivers must also
persist their own idempotency receipts and reject a key reused for a different
delivery. Use TLS and protect the shared secret.
