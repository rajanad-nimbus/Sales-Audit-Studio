'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { api, money } from '@/lib/api';
import { useToast } from '@/components/Feedback';
import { Alert, Badge, Button, Card, DataTable, Field, PageHeader } from '@/components/ui';
import { SearchSelect } from '@/components/SearchSelect';
import { StoreSelect } from '@/components/StoreSelect';
import { KINDS, describe, pretty } from '@/lib/corrections';
import type { Kind } from '@/lib/corrections';

type Tx = {
  id: string;
  business_date: string;
  transaction_type: string;
  signed_amount: string;
  currency: string;
  store_id: string;
  tender_type: string | null;
  payment_reference: string | null;
  customer_id: string | null;
  customer_identification_type: string | null;
  cashier_id: string | null;
  salesperson_id: string | null;
  customer_attributes: Record<string, string> | null;
  user_references: Record<string, string> | null;
  paid_out_details?: Record<string, string> | null;
  reconciliation_status: string;
};
type Facets = { stores: string[]; tenders: string[]; transaction_types: string[] };
type DraftLine = {
  item_code: string;
  description: string;
  quantity: string;
  unit_price: string;
  original_unit_price: string;
  price_override_reason: string;
  return_reason: string;
  item_type: string;
  item_status: string;
  selling_uom: string;
  uom_quantity: string;
  line_salesperson_id: string;
  swiped_or_scanned_at_pos: string;
  taxable: string;
  drop_ship: string;
  pump: string;
  recipient_state: string;
  recipient_country: string;
  reference_8: string;
  customer_order_no: string;
  customer_order_line_no: string;
  customer_order_date: string;
  media_id: string;
  selling_item: string;
  sales_type: string;
  fulfillment_order_no: string;
  return_warehouse: string;
  return_without_inventory: string;
  return_disposition: string;
  tax_rate: string;
  tax_code: string;
};
type DraftTender = {
  tender_type: string;
  amount: string;
  reference: string;
  authorization: string;
};
type DraftDiscount = { kind: string; code: string; amount: string; line_no: string };
type TaxRow = {
  id: string;
  tax_code: string;
  tax_name: string;
  rate: string;
  taxable_amount: string;
  tax_amount: string;
};
type DiscountRow = {
  id: string;
  kind: string;
  code: string | null;
  description: string | null;
  line_no: number | null;
  amount: string;
};

const UTC_OFFSETS = Array.from({ length: 105 }, (_, index) => -720 + index * 15);

function formatUtcOffset(minutes: number) {
  const sign = minutes < 0 ? '−' : '+';
  const absolute = Math.abs(minutes);
  return `UTC${sign}${String(Math.floor(absolute / 60)).padStart(2, '0')}:${String(absolute % 60).padStart(2, '0')}`;
}

function eventTimestamp(localDateTime: string, offset: string) {
  if (!localDateTime || offset === '') return undefined;
  const minutes = Number(offset);
  const sign = minutes < 0 ? '-' : '+';
  const absolute = Math.abs(minutes);
  return `${localDateTime}:00${sign}${String(Math.floor(absolute / 60)).padStart(2, '0')}:${String(absolute % 60).padStart(2, '0')}`;
}

export default function NewCorrectionPage() {
  const router = useRouter();
  const toast = useToast();
  const [error, setError] = useState<string | null>(null);
  const [facets, setFacets] = useState<Facets>({
    stores: [],
    tenders: [],
    transaction_types: [],
  });
  const [itemQuery, setItemQuery] = useState('');
  const [itemOptions, setItemOptions] = useState<{ value: string; label: string }[]>([]);
  const [selectedItemLines, setSelectedItemLines] = useState<
    {
      line_no: number;
      item_code: string;
      description: string | null;
      tax_code: string | null;
      tax_rate: string;
      tax_amount: string;
    }[]
  >([]);
  const [selectedItemLineNo, setSelectedItemLineNo] = useState('');
  const [selectedTenderLines, setSelectedTenderLines] = useState<
    { id: string; tender_type: string; amount: string; reference: string | null }[]
  >([]);
  const [selectedTenderId, setSelectedTenderId] = useState('');
  const [selectedTaxRows, setSelectedTaxRows] = useState<TaxRow[]>([]);
  const [selectedTaxId, setSelectedTaxId] = useState('');
  const [selectedDiscountRows, setSelectedDiscountRows] = useState<DiscountRow[]>([]);
  const [selectedDiscountId, setSelectedDiscountId] = useState('');
  const [discountOperation, setDiscountOperation] = useState<'add' | 'update' | 'delete'>('update');
  const [discountKind, setDiscountKind] = useState('Promotion');
  const [discountCode, setDiscountCode] = useState('');
  const [discountDescription, setDiscountDescription] = useState('');
  const [discountLineNo, setDiscountLineNo] = useState('');
  const [discountAmount, setDiscountAmount] = useState('');
  const [taxCode, setTaxCode] = useState('');
  const [taxName, setTaxName] = useState('');
  const [taxRate, setTaxRate] = useState('');
  const [taxableAmount, setTaxableAmount] = useState('');
  const [taxAmount, setTaxAmount] = useState('');
  const [userReferenceField, setUserReferenceField] = useState('reference_1');
  const [paidOutField, setPaidOutField] = useState('vendor_number');
  const [clearUserReference, setClearUserReference] = useState(false);
  const [kind, setKind] = useState<Kind>('Amount');
  const [search, setSearch] = useState('');
  const [results, setResults] = useState<Tx[]>([]);
  const [searching, setSearching] = useState(false);
  const [picked, setPicked] = useState<Tx | null>(null);
  const [transactionId, setTransactionId] = useState('');
  const [rationale, setRationale] = useState('');
  const [amount, setAmount] = useState('0');
  const [value, setValue] = useState('');
  const [store, setStore] = useState('');
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [registerId, setRegisterId] = useState('');
  const [transactionNumber, setTransactionNumber] = useState('');
  const [transactionLocalDateTime, setTransactionLocalDateTime] = useState('');
  const [transactionUtcOffset, setTransactionUtcOffset] = useState('');
  const [customerId, setCustomerId] = useState('');
  const [customerIdentificationType, setCustomerIdentificationType] = useState('');
  const [customerAttributeType, setCustomerAttributeType] = useState('');
  const [clearCustomerAttribute, setClearCustomerAttribute] = useState(false);
  const [cashierId, setCashierId] = useState('');
  const [salespersonId, setSalespersonId] = useState('');
  const [clearCashier, setClearCashier] = useState(false);
  const [clearSalesperson, setClearSalesperson] = useState(false);
  const [userReferences, setUserReferences] = useState(['', '', '', '']);
  const [tender, setTender] = useState('Cash');
  const [manualLines, setManualLines] = useState<DraftLine[]>([]);
  const [manualTenders, setManualTenders] = useState<DraftTender[]>([]);
  const [manualDiscounts, setManualDiscounts] = useState<DraftDiscount[]>([]);
  const [transactionType, setTransactionType] = useState('Sale');
  const [missingSourceSystem, setMissingSourceSystem] = useState('POS');
  const [refundSource, setRefundSource] = useState('Processor');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const requestedKind = params.get('kind');
    if (requestedKind && KINDS.some((entry) => entry.key === requestedKind))
      setKind(requestedKind as Kind);
    if (params.has('transaction_id')) setTransactionId(params.get('transaction_id') ?? '');
    if (params.has('discount_id')) setSelectedDiscountId(params.get('discount_id') ?? '');
    if (params.has('line_no')) setSelectedItemLineNo(params.get('line_no') ?? '');
    if (params.has('attribute_type')) setCustomerAttributeType(params.get('attribute_type') ?? '');
    if (params.has('store')) setStore(params.get('store') ?? '');
    if (params.has('date')) setDate(params.get('date') ?? '');
    if (params.has('register')) setRegisterId(params.get('register') ?? '');
    if (params.has('transaction_number'))
      setTransactionNumber(params.get('transaction_number') ?? '');
  }, []);

  useEffect(() => {
    api
      .get('/api/transaction-adjustments/transactions/facets')
      .then((r) => setFacets(r.data))
      .catch(() => {});
  }, []);

  useEffect(() => {
    const id = transactionId.trim();
    if (!['CustomerAttribute', 'Employee', 'Discount'].includes(kind)) return;
    if (id.length !== 36) {
      setPicked(null);
      return;
    }
    setPicked(null);
    let active = true;
    api
      .get<Tx>(`/api/transaction-adjustments/transactions/${id}`)
      .then(({ data }) => {
        if (active) setPicked(data);
      })
      .catch((e: any) => {
        if (active)
          setError(e?.response?.data?.detail ?? 'Transaction details could not be loaded.');
      });
    return () => {
      active = false;
    };
  }, [kind, transactionId]);

  useEffect(() => {
    const query = itemQuery.trim();
    if (query.length < 2) {
      setItemOptions([]);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      api
        .get('/api/foundation', { params: { category: 'Item', q: query, limit: 50 } })
        .then((response) => {
          if (!cancelled) {
            setItemOptions(
              response.data.items.map((item: { code: string; name: string }) => ({
                value: item.code,
                label: `${item.code} · ${item.name}`,
              }))
            );
          }
        })
        .catch((e: any) => {
          if (!cancelled)
            setError(e?.response?.data?.detail ?? 'Unable to search item references.');
        });
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [itemQuery]);

  useEffect(() => {
    const id = transactionId.trim();
    if (!['Item', 'TaxLine', 'Discount'].includes(kind) || id.length !== 36) {
      setSelectedItemLines([]);
      setSelectedItemLineNo('');
      return;
    }
    let active = true;
    api
      .get<{
        items: {
          line_no: number;
          item_code: string;
          description: string | null;
          tax_code: string | null;
          tax_rate: string;
          tax_amount: string;
        }[];
      }>(`/api/transactions/${id}/item-lines`)
      .then(({ data }) => {
        if (!active) return;
        setSelectedItemLines(data.items);
        setSelectedItemLineNo((current) =>
          data.items.some((line) => String(line.line_no) === current)
            ? current
            : String(data.items[0]?.line_no ?? '')
        );
      })
      .catch((e: any) => {
        if (active) {
          setSelectedItemLines([]);
          setSelectedItemLineNo('');
          setError(e?.response?.data?.detail ?? 'Transaction item lines could not be loaded.');
        }
      });
    return () => {
      active = false;
    };
  }, [kind, transactionId]);

  useEffect(() => {
    if (kind !== 'TaxLine') return;
    const line = selectedItemLines.find((item) => String(item.line_no) === selectedItemLineNo);
    if (!line) return;
    setTaxCode(line.tax_code ?? '');
    setTaxRate(String(Number(line.tax_rate) * 100));
    setTaxAmount(line.tax_amount);
  }, [kind, selectedItemLines, selectedItemLineNo]);

  useEffect(() => {
    const id = transactionId.trim();
    if (kind !== 'Discount' || id.length !== 36) {
      setSelectedDiscountRows([]);
      if (kind !== 'Discount') setSelectedDiscountId('');
      return;
    }
    let active = true;
    api
      .get<{ items: DiscountRow[] }>(`/api/transactions/${id}/discount-lines`)
      .then(({ data }) => {
        if (!active) return;
        setSelectedDiscountRows(data.items);
        setSelectedDiscountId((current) =>
          data.items.some((row) => row.id === current) ? current : (data.items[0]?.id ?? '')
        );
      })
      .catch((e: any) => {
        if (active) {
          setSelectedDiscountRows([]);
          setSelectedDiscountId('');
          setError(e?.response?.data?.detail ?? 'Transaction discounts could not be loaded.');
        }
      });
    return () => {
      active = false;
    };
  }, [kind, transactionId]);

  useEffect(() => {
    if (discountOperation === 'add') return;
    const row = selectedDiscountRows.find((item) => item.id === selectedDiscountId);
    if (!row) return;
    setDiscountKind(row.kind);
    setDiscountCode(row.code ?? '');
    setDiscountDescription(row.description ?? '');
    setDiscountLineNo(row.line_no ? String(row.line_no) : '');
    setDiscountAmount(row.amount);
  }, [kind, discountOperation, selectedDiscountRows, selectedDiscountId]);

  useEffect(() => {
    const id = transactionId.trim();
    if (kind !== 'TaxDetail' || id.length !== 36) {
      setSelectedTaxRows([]);
      setSelectedTaxId('');
      return;
    }
    let active = true;
    api
      .get<{ items: TaxRow[] }>(`/api/transactions/${id}/tax-lines`)
      .then(({ data }) => {
        if (!active) return;
        setSelectedTaxRows(data.items);
        const next = data.items[0];
        setSelectedTaxId(next?.id ?? '');
        setTaxCode(next?.tax_code ?? '');
        setTaxName(next?.tax_name ?? '');
        setTaxRate(next ? String(Number(next.rate) * 100) : '');
        setTaxableAmount(next?.taxable_amount ?? '');
        setTaxAmount(next?.tax_amount ?? '');
      })
      .catch((e: any) => {
        if (active) {
          setSelectedTaxRows([]);
          setSelectedTaxId('');
          setError(e?.response?.data?.detail ?? 'Transaction tax rows could not be loaded.');
        }
      });
    return () => {
      active = false;
    };
  }, [kind, transactionId]);

  useEffect(() => {
    const id = transactionId.trim();
    if (!['Tender', 'Reference'].includes(kind) || id.length !== 36) {
      setSelectedTenderLines([]);
      setSelectedTenderId('');
      return;
    }
    let active = true;
    api
      .get<{
        items: { id: string; tender_type: string; amount: string; reference: string | null }[];
      }>(`/api/transactions/${id}/tender-lines`)
      .then(({ data }) => {
        if (!active) return;
        setSelectedTenderLines(data.items);
        setSelectedTenderId((current) => {
          if (data.items.length === 1) return data.items[0].id;
          return data.items.some((line) => line.id === current) ? current : '';
        });
      })
      .catch((e: any) => {
        if (active) {
          setSelectedTenderLines([]);
          setSelectedTenderId('');
          setError(e?.response?.data?.detail ?? 'Transaction tenders could not be loaded.');
        }
      });
    return () => {
      active = false;
    };
  }, [kind, transactionId]);

  const missing = kind === 'MissingTransaction';
  const active = KINDS.find((x) => x.key === kind)!;
  const hasPrecision = (value: string, places: number) => {
    const parsed = Number(value);
    const scaled = parsed * 10 ** places;
    return Number.isFinite(parsed) && Math.abs(scaled - Math.round(scaled)) < 1e-8;
  };
  const receiptPreview = useMemo(() => {
    const cents = (value: number) => Math.round((value + Number.EPSILON) * 100) / 100;
    const gross = manualLines.reduce(
      (sum, line) => sum + cents(Number(line.quantity || 0) * Number(line.unit_price || 0)),
      0
    );
    const tax = manualLines.reduce((sum, line) => {
      const lineGross = cents(Number(line.quantity || 0) * Number(line.unit_price || 0));
      return sum + cents((lineGross * Number(line.tax_rate || 0)) / 100);
    }, 0);
    const discounts = manualDiscounts.reduce((sum, row) => sum + Number(row.amount || 0), 0);
    const tendered = manualTenders.reduce((sum, row) => sum + Number(row.amount || 0), 0);
    const expected = cents(gross - discounts + tax);
    const totalVariance = cents(expected - Number(amount || 0));
    const tenderVariance = cents(tendered - Number(amount || 0));
    return { gross, tax, discounts, tendered, expected, totalVariance, tenderVariance };
  }, [manualLines, manualDiscounts, manualTenders, amount]);
  const payload = useMemo(() => {
    if (kind === 'MissingTransaction') {
      const lines = manualLines
        .filter((line) => line.item_code.trim() && line.quantity && line.unit_price)
        .map((line) => {
          const quantity = Number(line.quantity);
          const unitPrice = Number(line.unit_price);
          const taxRate = Number(line.tax_rate || 0) / 100;
          const gross = Math.round((quantity * unitPrice + Number.EPSILON) * 100) / 100;
          const tax = Math.round((gross * taxRate + Number.EPSILON) * 100) / 100;
          const customerOrderAttributes = {
            ...(line.item_type.trim() ? { item_type: line.item_type.trim() } : {}),
            ...(line.item_status.trim() ? { item_status: line.item_status.trim() } : {}),
            ...(line.selling_uom.trim() ? { selling_uom: line.selling_uom.trim() } : {}),
            ...(line.uom_quantity.trim() ? { uom_quantity: Number(line.uom_quantity) } : {}),
            ...(line.line_salesperson_id.trim()
              ? { salesperson_id: line.line_salesperson_id.trim() }
              : {}),
            ...(line.swiped_or_scanned_at_pos
              ? { swiped_or_scanned_at_pos: line.swiped_or_scanned_at_pos === 'true' }
              : {}),
            ...(line.taxable ? { taxable: line.taxable === 'true' } : {}),
            ...(line.drop_ship ? { drop_ship: line.drop_ship === 'true' } : {}),
            ...(line.pump.trim() ? { pump: line.pump.trim() } : {}),
            ...(line.recipient_state.trim()
              ? { recipient_state: line.recipient_state.trim() }
              : {}),
            ...(line.recipient_country.trim()
              ? { recipient_country: line.recipient_country.trim() }
              : {}),
            ...(line.reference_8.trim() ? { reference_8: line.reference_8.trim() } : {}),
            ...(line.customer_order_no.trim()
              ? { customer_order_no: line.customer_order_no.trim() }
              : {}),
            ...(line.customer_order_line_no.trim()
              ? { customer_order_line_no: line.customer_order_line_no.trim() }
              : {}),
            ...(line.customer_order_date ? { customer_order_date: line.customer_order_date } : {}),
            ...(line.media_id.trim() ? { media_id: line.media_id.trim() } : {}),
            ...(line.selling_item.trim() ? { selling_item: line.selling_item.trim() } : {}),
            ...(line.sales_type.trim() ? { sales_type: line.sales_type.trim() } : {}),
            ...(line.fulfillment_order_no.trim()
              ? { fulfillment_order_no: line.fulfillment_order_no.trim() }
              : {}),
            ...(line.return_warehouse.trim()
              ? { return_warehouse: line.return_warehouse.trim() }
              : {}),
            ...(line.return_without_inventory
              ? { return_without_inventory: line.return_without_inventory === 'true' }
              : {}),
            ...(line.return_disposition.trim()
              ? { return_disposition: line.return_disposition.trim() }
              : {}),
          };
          return {
            item_code: line.item_code.trim(),
            description: line.description.trim() || undefined,
            quantity,
            unit_price: unitPrice,
            ...(line.original_unit_price.trim()
              ? { original_unit_price: Number(line.original_unit_price) }
              : {}),
            ...(line.price_override_reason.trim()
              ? { price_override_reason: line.price_override_reason.trim() }
              : {}),
            ...(line.return_reason.trim() && ['Return', 'Refund'].includes(transactionType)
              ? { return_reason: line.return_reason.trim() }
              : {}),
            ...(Object.keys(customerOrderAttributes).length
              ? { source_attributes: customerOrderAttributes }
              : {}),
            gross_amount: gross,
            tax_rate: taxRate,
            tax_amount: tax,
            tax_code: line.tax_code.trim() || undefined,
          };
        });
      const tenders = manualTenders
        .filter((row) => row.amount && Number(row.amount) > 0)
        .map((row) => ({
          tender_type: row.tender_type,
          amount: Number(row.amount),
          reference: row.reference.trim() || undefined,
          authorization: row.authorization.trim() || undefined,
        }));
      const discounts = manualDiscounts
        .filter((row) => row.amount && Number(row.amount) > 0)
        .map((row) => ({
          kind: row.kind,
          code: row.code.trim() || undefined,
          ...(row.line_no ? { line_no: Number(row.line_no) } : {}),
          amount: Number(row.amount),
        }));
      const references = userReferences
        .map((value, index) => ({ field: index + 1, value: value.trim() }))
        .filter((reference) => reference.value);
      return {
        business_date: date,
        ...(eventTimestamp(transactionLocalDateTime, transactionUtcOffset)
          ? { event_timestamp: eventTimestamp(transactionLocalDateTime, transactionUtcOffset) }
          : {}),
        store_id: store,
        register_id: registerId || undefined,
        transaction_number: transactionNumber || undefined,
        customer_id: customerId.trim() || undefined,
        customer_identification_type: customerIdentificationType.trim() || undefined,
        cashier_id: cashierId.trim() || undefined,
        salesperson_id: salespersonId.trim() || undefined,
        ...(references.length ? { user_references: references } : {}),
        amount: Number(amount),
        transaction_type: transactionType,
        ...(['Sale', 'Return'].includes(transactionType)
          ? { source_system: missingSourceSystem }
          : {}),
        ...(transactionType === 'Refund' ? { source_system: refundSource } : {}),
        tender_type: tenders[0]?.tender_type ?? tender,
        ...(lines.length ? { lines } : {}),
        ...(tenders.length ? { tenders } : {}),
        ...(discounts.length ? { discounts } : {}),
      };
    }
    if (kind === 'Amount') return { amount_delta: Number(amount) };
    if (kind === 'Tender')
      return { tender_type: value, ...(selectedTenderId ? { tender_id: selectedTenderId } : {}) };
    if (kind === 'Tax') return { tax_delta: Number(amount) };
    if (kind === 'TaxDetail')
      return {
        tax_id: selectedTaxId,
        tax_code: taxCode.trim(),
        tax_name: taxName.trim(),
        rate: String(Number(taxRate) / 100),
        taxable_amount: taxableAmount,
        tax_amount: taxAmount,
      };
    if (kind === 'TaxLine')
      return {
        line_no: Number(selectedItemLineNo),
        tax_code: taxCode.trim() || null,
        rate: String(Number(taxRate) / 100),
        tax_amount: taxAmount,
      };
    if (kind === 'Discount')
      return discountOperation === 'delete'
        ? { operation: 'delete', discount_id: selectedDiscountId }
        : {
            operation: discountOperation,
            ...(discountOperation === 'update' ? { discount_id: selectedDiscountId } : {}),
            kind: discountKind,
            code: discountCode.trim() || null,
            description: discountDescription.trim() || null,
            line_no: discountLineNo ? Number(discountLineNo) : null,
            amount: discountAmount,
          };
    if (kind === 'Reference')
      return { reference: value, ...(selectedTenderId ? { tender_id: selectedTenderId } : {}) };
    if (kind === 'Customer')
      return {
        ...(customerId.trim() ? { customer_id: customerId.trim() } : {}),
        ...(customerIdentificationType.trim()
          ? { customer_identification_type: customerIdentificationType.trim() }
          : {}),
      };
    if (kind === 'Employee')
      return {
        ...(clearCashier
          ? { cashier_id: null }
          : cashierId.trim()
            ? { cashier_id: cashierId.trim() }
            : {}),
        ...(clearSalesperson
          ? { salesperson_id: null }
          : salespersonId.trim()
            ? { salesperson_id: salespersonId.trim() }
            : {}),
      };
    if (kind === 'CustomerAttribute')
      return {
        attribute_type: customerAttributeType.trim(),
        value: clearCustomerAttribute ? '' : value.trim(),
      };
    if (kind === 'UserReference')
      return {
        reference_field: userReferenceField,
        value: clearUserReference ? '' : value.trim(),
      };
    if (kind === 'PaidOutDetail') return { field: paidOutField, value: value.trim() };
    return { item: value, line_no: Number(selectedItemLineNo) };
  }, [
    kind,
    date,
    store,
    registerId,
    transactionNumber,
    transactionLocalDateTime,
    transactionUtcOffset,
    customerId,
    customerIdentificationType,
    customerAttributeType,
    clearCustomerAttribute,
    cashierId,
    salespersonId,
    clearCashier,
    clearSalesperson,
    userReferences,
    amount,
    transactionType,
    missingSourceSystem,
    refundSource,
    tender,
    manualLines,
    manualTenders,
    manualDiscounts,
    value,
    selectedItemLineNo,
    selectedTenderId,
    selectedTaxId,
    selectedDiscountId,
    discountOperation,
    discountKind,
    discountCode,
    discountDescription,
    discountLineNo,
    discountAmount,
    taxCode,
    taxName,
    taxRate,
    taxableAmount,
    taxAmount,
    userReferenceField,
    clearUserReference,
    paidOutField,
  ]);
  const valid =
    !!rationale.trim() &&
    (missing
      ? !!store.trim() &&
        Number.isFinite(Number(amount)) &&
        Number(amount) > 0 &&
        hasPrecision(amount, 2) &&
        (!transactionLocalDateTime ||
          /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(transactionLocalDateTime)) &&
        (!transactionLocalDateTime || transactionUtcOffset !== '') &&
        (!transactionUtcOffset || !!transactionLocalDateTime)
      : !!transactionId.trim()) &&
    (!missing ||
      (manualLines.every(
        (line) =>
          line.item_code.trim() &&
          line.quantity.trim() &&
          line.unit_price.trim() &&
          Number(line.quantity) > 0 &&
          hasPrecision(line.quantity, 3) &&
          Number.isFinite(Number(line.unit_price)) &&
          hasPrecision(line.unit_price, 2) &&
          Number(line.unit_price) >= 0 &&
          (!line.original_unit_price.trim() ||
            (Number.isFinite(Number(line.original_unit_price)) &&
              hasPrecision(line.original_unit_price, 2) &&
              Number(line.original_unit_price) >= 0)) &&
          line.price_override_reason.length <= 255 &&
          line.return_reason.length <= 255 &&
          [
            line.item_type,
            line.item_status,
            line.selling_uom,
            line.line_salesperson_id,
            line.pump,
            line.recipient_state,
            line.recipient_country,
            line.reference_8,
          ].every((field) => field.length <= 255) &&
          (!line.uom_quantity.trim() ||
            (Number.isFinite(Number(line.uom_quantity)) &&
              Number(line.uom_quantity) > 0 &&
              hasPrecision(line.uom_quantity, 3))) &&
          [
            line.customer_order_no,
            line.customer_order_line_no,
            line.media_id,
            line.selling_item,
            line.sales_type,
            line.fulfillment_order_no,
            line.return_warehouse,
            line.return_disposition,
          ].every((field) => field.length <= 255) &&
          (!line.customer_order_date || /^\d{4}-\d{2}-\d{2}$/.test(line.customer_order_date)) &&
          hasPrecision(line.tax_rate || '0', 2) &&
          Number(line.tax_rate || 0) >= 0 &&
          Number(line.tax_rate || 0) <= 100
      ) &&
        manualTenders.every(
          (row) =>
            row.amount &&
            Number.isFinite(Number(row.amount)) &&
            hasPrecision(row.amount, 2) &&
            Number(row.amount) > 0
        ) &&
        (!manualDiscounts.length || manualLines.length > 0) &&
        manualDiscounts.every(
          (row) =>
            row.amount &&
            Number.isFinite(Number(row.amount)) &&
            hasPrecision(row.amount, 2) &&
            Number(row.amount) > 0 &&
            (!row.line_no ||
              (Number.isInteger(Number(row.line_no)) &&
                Number(row.line_no) >= 1 &&
                Number(row.line_no) <= manualLines.length))
        ))) &&
    (kind === 'Amount' || kind === 'Tax'
      ? Number.isFinite(Number(amount))
      : kind === 'TaxDetail'
        ? !!selectedTaxId &&
          !!taxCode.trim() &&
          !!taxName.trim() &&
          Number.isFinite(Number(taxRate)) &&
          Number(taxRate) >= 0 &&
          Number(taxRate) <= 100 &&
          hasPrecision(taxRate, 2) &&
          Number.isFinite(Number(taxableAmount)) &&
          hasPrecision(taxableAmount, 2) &&
          Number.isFinite(Number(taxAmount)) &&
          hasPrecision(taxAmount, 2)
        : kind === 'TaxLine'
          ? !!selectedItemLineNo &&
            selectedItemLines.length > 0 &&
            Number.isFinite(Number(taxRate)) &&
            Number(taxRate) >= 0 &&
            Number(taxRate) <= 100 &&
            hasPrecision(taxRate, 2) &&
            Number.isFinite(Number(taxAmount)) &&
            hasPrecision(taxAmount, 2)
          : kind === 'Discount'
            ? (() => {
                const row = selectedDiscountRows.find((item) => item.id === selectedDiscountId);
                const amountValue = Number(discountAmount);
                if (discountOperation === 'delete') return !!row;
                if (discountOperation === 'update' && !row) return false;
                const signReference =
                  discountOperation === 'update' ? row?.amount : picked?.signed_amount;
                const amountSignMatches =
                  !signReference ||
                  amountValue === 0 ||
                  Number(signReference) === 0 ||
                  amountValue * Number(signReference) > 0;
                return (
                  ['Promotion', 'Coupon', 'Voucher', 'Manual'].includes(discountKind) &&
                  !!discountAmount.trim() &&
                  Number.isFinite(amountValue) &&
                  hasPrecision(discountAmount, 2) &&
                  Math.abs(amountValue) <= 9999999999999.99 &&
                  (discountOperation !== 'add' || amountValue !== 0) &&
                  (discountOperation !== 'add' || !!picked) &&
                  amountSignMatches &&
                  discountCode.length <= 100 &&
                  discountDescription.length <= 255 &&
                  (!discountLineNo ||
                    selectedItemLines.some((line) => String(line.line_no) === discountLineNo))
                );
              })()
            : kind === 'Item'
              ? !!value.trim() && !!selectedItemLineNo && selectedItemLines.length > 0
              : ['Tender', 'Reference'].includes(kind)
                ? !!value.trim() && (selectedTenderLines.length <= 1 || !!selectedTenderId)
                : kind === 'Customer'
                  ? !!customerId.trim() || !!customerIdentificationType.trim()
                  : kind === 'Employee'
                    ? clearCashier ||
                      clearSalesperson ||
                      !!cashierId.trim() ||
                      !!salespersonId.trim()
                    : kind === 'CustomerAttribute'
                      ? !!customerAttributeType.trim() && (clearCustomerAttribute || !!value.trim())
                      : kind === 'UserReference'
                        ? !!userReferenceField && (clearUserReference || !!value.trim())
                        : kind === 'PaidOutDetail'
                          ? !!paidOutField && !!value.trim()
                          : missing || !!value.trim());

  const pickKind = (k: Kind) => {
    setKind(k);
    setValue('');
    setAmount('0');
    setClearUserReference(false);
    if (k === 'Customer') {
      setCustomerId('');
      setCustomerIdentificationType('');
    }
    if (k === 'Employee') {
      setCashierId('');
      setSalespersonId('');
      setClearCashier(false);
      setClearSalesperson(false);
    }
    if (k === 'Discount') {
      setDiscountOperation('update');
      setSelectedDiscountId('');
      setDiscountKind('Promotion');
      setDiscountCode('');
      setDiscountDescription('');
      setDiscountLineNo('');
      setDiscountAmount('');
    }
    if (k === 'CustomerAttribute') {
      setCustomerAttributeType('');
      setClearCustomerAttribute(false);
    }
    if (k === 'UserReference') setUserReferenceField('reference_1');
  };
  const lookup = async () => {
    if (search.trim().length < 2) return;
    try {
      setSearching(true);
      setResults(
        (
          await api.get('/api/transaction-adjustments/transactions/search', {
            params: { q: search.trim() },
          })
        ).data
      );
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Transaction search failed.');
    } finally {
      setSearching(false);
    }
  };
  const selectTransaction = (tx: Tx) => {
    setPicked(tx);
    setTransactionId(tx.id);
    setSearch('');
    setResults([]);
    if (kind === 'Tender' && tx.tender_type) setValue(tx.tender_type);
    if (kind === 'Customer') {
      setCustomerId('');
      setCustomerIdentificationType('');
    }
    setError(null);
  };
  const clearTransaction = () => {
    setPicked(null);
    setTransactionId('');
    setSelectedItemLines([]);
    setSelectedItemLineNo('');
    setSelectedTenderLines([]);
    setSelectedTenderId('');
  };
  const submit = async () => {
    setBusy(true);
    try {
      await api.post('/api/transaction-adjustments', {
        transaction_id: missing ? null : transactionId.trim(),
        adjustment_type: kind,
        proposed_values: payload,
        rationale: rationale.trim(),
      });
      toast('Correction submitted for Finance review.', 'success');
      router.push('/corrections');
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Unable to submit correction.');
      setBusy(false);
    }
  };

  const tenders = facets.tenders.length ? facets.tenders : ['Cash', 'Card', 'GiftCard'];
  const types = facets.transaction_types.length ? facets.transaction_types : ['Sale', 'Return'];

  return (
    <div className="page-container">
      <Link href="/corrections" className="back-link">
        ← All corrections
      </Link>
      <PageHeader
        title="New correction"
        subtitle="Choose the correction, identify the source transaction, and give Finance the evidence-based rationale. The source record stays unchanged."
      />
      {error && (
        <Alert tone="error" onDismiss={() => setError(null)}>
          {error}
        </Alert>
      )}
      <div className="corr-layout">
        <div className="corr-steps">
          <Card title="1. What needs correcting?">
            <div className="ac-tiles">
              {KINDS.map((k) => (
                <button
                  key={k.key}
                  className="ac-tile"
                  aria-pressed={kind === k.key}
                  onClick={() => pickKind(k.key)}
                >
                  <b>{k.title}</b>
                  <em>{k.help}</em>
                </button>
              ))}
            </div>
          </Card>

          <Card title={missing ? '2. The missing transaction' : '2. Which transaction?'}>
            {missing ? (
              <div className="cfg-form">
                <Field label="Store">
                  <StoreSelect value={store} onChange={setStore} />
                </Field>
                <Field label="Business date">
                  <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
                </Field>
                <Field
                  label="Transaction date and time"
                  hint="Enter the store’s local time, then select its UTC offset on that date. This preserves the exact event time across time zones."
                >
                  <input
                    type="datetime-local"
                    value={transactionLocalDateTime}
                    onChange={(e) => setTransactionLocalDateTime(e.target.value)}
                  />
                </Field>
                <Field label="UTC offset at transaction time">
                  <select
                    value={transactionUtcOffset}
                    onChange={(e) => setTransactionUtcOffset(e.target.value)}
                    disabled={!transactionLocalDateTime}
                  >
                    <option value="">Select offset</option>
                    {UTC_OFFSETS.map((offset) => (
                      <option key={offset} value={offset}>
                        {formatUtcOffset(offset)}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Register">
                  <input value={registerId} onChange={(e) => setRegisterId(e.target.value)} />
                </Field>
                <Field label="Missing transaction number">
                  <input
                    value={transactionNumber}
                    onChange={(e) => setTransactionNumber(e.target.value)}
                  />
                </Field>
                <Field
                  label="Customer reference"
                  hint="Optional ID from the receipt or order. Names and contact details are not collected."
                >
                  <input
                    value={customerId}
                    onChange={(e) => setCustomerId(e.target.value)}
                    maxLength={100}
                  />
                </Field>
                <Field
                  label="Customer identification type"
                  hint="Optional source of the customer reference, such as loyalty account or external ID."
                >
                  <input
                    value={customerIdentificationType}
                    onChange={(e) => setCustomerIdentificationType(e.target.value)}
                    maxLength={50}
                  />
                </Field>
                <Field
                  label="Salesperson ID"
                  hint="Optional employee identifier recorded on the receipt."
                >
                  <input
                    value={salespersonId}
                    onChange={(e) => setSalespersonId(e.target.value)}
                    maxLength={100}
                  />
                </Field>
                <Field
                  label="Cashier ID"
                  hint="Optional employee identifier for the cashier who recorded the transaction."
                >
                  <input
                    value={cashierId}
                    onChange={(e) => setCashierId(e.target.value)}
                    maxLength={100}
                  />
                </Field>
                <div className="section-heading">
                  <h3>Retailer reference fields</h3>
                </div>
                <div className="cfg-form">
                  {userReferences.map((reference, index) => (
                    <Field key={`reference-${index}`} label={`Reference ${index + 1}`}>
                      <input
                        value={reference}
                        onChange={(e) =>
                          setUserReferences((values) =>
                            values.map((value, i) => (i === index ? e.target.value : value))
                          )
                        }
                        maxLength={255}
                      />
                    </Field>
                  ))}
                </div>
              </div>
            ) : picked || transactionId ? (
              <Alert
                tone="success"
                action={
                  <Button size="sm" onClick={clearTransaction}>
                    Change
                  </Button>
                }
              >
                {picked ? (
                  <>
                    <b>{picked.id}</b> · {picked.store_id} · {picked.business_date} ·{' '}
                    {picked.transaction_type} {money(picked.signed_amount, picked.currency)}
                    {picked.tender_type ? ` · ${picked.tender_type}` : ''}
                  </>
                ) : (
                  <>
                    Transaction <b>{transactionId}</b>
                  </>
                )}
              </Alert>
            ) : (
              <>
                <p className="hint">
                  Search by canonical ID, payment reference, settlement reference or store.
                </p>
                <div className="form-row">
                  <div className="grow">
                    <input
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') lookup();
                      }}
                      placeholder="Search transactions"
                      aria-label="Search transactions"
                    />
                  </div>
                  <Button disabled={searching || search.trim().length < 2} onClick={lookup}>
                    {searching ? 'Searching…' : 'Search'}
                  </Button>
                </div>
                {results.length > 0 && (
                  <DataTable
                    columns={[
                      {
                        key: 'tx',
                        header: 'Transaction',
                        primary: true,
                        render: (tx) => (
                          <>
                            <code style={{ fontSize: 11 }}>{tx.id}</code>
                            <small className="act-sub" style={{ display: 'block' }}>
                              {tx.payment_reference || 'No payment reference'}
                            </small>
                          </>
                        ),
                        csvValue: (tx) =>
                          `${tx.id} · ${tx.payment_reference || 'No payment reference'}`,
                      },
                      {
                        key: 'where',
                        header: 'Store / date',
                        render: (tx) => `${tx.store_id} · ${tx.business_date}`,
                        csvValue: (tx) => `${tx.store_id} · ${tx.business_date}`,
                      },
                      {
                        key: 'type',
                        header: 'Type',
                        render: (tx) => tx.transaction_type,
                        csvValue: (tx) => tx.transaction_type,
                      },
                      {
                        key: 'amt',
                        header: 'Amount',
                        numeric: true,
                        render: (tx) => money(tx.signed_amount, tx.currency),
                        csvValue: (tx) => `${tx.signed_amount} ${tx.currency}`,
                      },
                      {
                        key: 'tender',
                        header: 'Tender',
                        render: (tx) => tx.tender_type || '—',
                        csvValue: (tx) => tx.tender_type || '—',
                      },
                    ]}
                    rows={results}
                    rowKey={(tx) => tx.id}
                    onRowClick={selectTransaction}
                    csvFileName="correction-transaction-search-visible.csv"
                  />
                )}
                <details className="cfg-details">
                  <summary>Already have the ID? Paste it</summary>
                  <Field label="Canonical transaction ID">
                    <input
                      value={transactionId}
                      onChange={(e) => setTransactionId(e.target.value)}
                      placeholder="Paste an ID"
                    />
                  </Field>
                </details>
              </>
            )}
          </Card>

          <Card title="3. The correction">
            {(kind === 'Amount' || kind === 'Tax' || missing) && (
              <div className="cfg-form">
                <Field
                  label={missing ? 'Amount' : 'Adjustment amount'}
                  hint={
                    missing
                      ? 'Positive amount of the missing transaction.'
                      : 'Use a negative number to reduce.'
                  }
                >
                  <input
                    type="number"
                    step="0.01"
                    value={amount}
                    onChange={(e) => setAmount(e.target.value)}
                  />
                </Field>
                {missing && (
                  <>
                    <Field label="Transaction type">
                      <select
                        value={transactionType}
                        onChange={(e) => setTransactionType(e.target.value)}
                      >
                        {types.map((x) => (
                          <option key={x}>{x}</option>
                        ))}
                      </select>
                    </Field>
                    {['Sale', 'Return'].includes(transactionType) && (
                      <Field label="Source system">
                        <select
                          value={missingSourceSystem}
                          onChange={(e) => setMissingSourceSystem(e.target.value)}
                        >
                          <option>POS</option>
                          <option>OMS</option>
                        </select>
                      </Field>
                    )}
                    {transactionType === 'Refund' && (
                      <Field label="Refund source">
                        <select
                          value={refundSource}
                          onChange={(e) => setRefundSource(e.target.value)}
                        >
                          <option>Processor</option>
                          <option>Shopify</option>
                        </select>
                      </Field>
                    )}
                    <Field label="Tender">
                      <select value={tender} onChange={(e) => setTender(e.target.value)}>
                        {tenders.map((x) => (
                          <option key={x}>{x}</option>
                        ))}
                      </select>
                    </Field>
                  </>
                )}
              </div>
            )}
            {kind === 'TaxDetail' && (
              <div className="cfg-form">
                <Field label="Tax row">
                  <select
                    value={selectedTaxId}
                    onChange={(event) => {
                      const row = selectedTaxRows.find((item) => item.id === event.target.value);
                      setSelectedTaxId(event.target.value);
                      if (!row) return;
                      setTaxCode(row.tax_code);
                      setTaxName(row.tax_name);
                      setTaxRate(String(Number(row.rate) * 100));
                      setTaxableAmount(row.taxable_amount);
                      setTaxAmount(row.tax_amount);
                    }}
                  >
                    <option value="">Select a tax row</option>
                    {selectedTaxRows.map((row) => (
                      <option key={row.id} value={row.id}>
                        {row.tax_code} · {row.tax_name}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Tax code">
                  <input
                    maxLength={30}
                    value={taxCode}
                    onChange={(e) => setTaxCode(e.target.value)}
                  />
                </Field>
                <Field label="Tax name">
                  <input
                    maxLength={100}
                    value={taxName}
                    onChange={(e) => setTaxName(e.target.value)}
                  />
                </Field>
                <Field label="Rate %">
                  <input
                    type="number"
                    min="0"
                    max="100"
                    step="0.01"
                    value={taxRate}
                    onChange={(e) => setTaxRate(e.target.value)}
                  />
                </Field>
                <Field label="Taxable amount">
                  <input
                    type="number"
                    step="0.01"
                    value={taxableAmount}
                    onChange={(e) => setTaxableAmount(e.target.value)}
                  />
                </Field>
                <Field label="Tax amount">
                  <input
                    type="number"
                    step="0.01"
                    value={taxAmount}
                    onChange={(e) => setTaxAmount(e.target.value)}
                  />
                </Field>
              </div>
            )}
            {kind === 'TaxLine' && (
              <div className="cfg-form">
                <Field label="Item line">
                  <select
                    value={selectedItemLineNo}
                    onChange={(event) => setSelectedItemLineNo(event.target.value)}
                    disabled={selectedItemLines.length === 0}
                  >
                    {selectedItemLines.length === 0 && (
                      <option value="">No item lines found</option>
                    )}
                    {selectedItemLines.map((line) => (
                      <option key={line.line_no} value={line.line_no}>
                        Line {line.line_no} · {line.item_code}
                        {line.description ? ` · ${line.description}` : ''}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Tax code">
                  <input
                    maxLength={30}
                    value={taxCode}
                    onChange={(event) => setTaxCode(event.target.value)}
                    placeholder="Optional"
                  />
                </Field>
                <Field label="Rate %">
                  <input
                    type="number"
                    min="0"
                    max="100"
                    step="0.01"
                    value={taxRate}
                    onChange={(event) => setTaxRate(event.target.value)}
                  />
                </Field>
                <Field label="Tax amount">
                  <input
                    type="number"
                    step="0.01"
                    value={taxAmount}
                    onChange={(event) => setTaxAmount(event.target.value)}
                  />
                </Field>
              </div>
            )}
            {kind === 'Discount' && (
              <div className="stack stack-sm">
                <p className="hint">
                  Add a missing discount, correct one recorded discount, or remove an erroneous
                  entry. The amount must follow the transaction’s financial sign. Approved changes
                  refresh detail balance and invalidate store-day totals.
                </p>
                <Field label="Correction action">
                  <select
                    value={discountOperation}
                    onChange={(event) => {
                      const next = event.target.value as 'add' | 'update' | 'delete';
                      setDiscountOperation(next);
                      if (next === 'add') {
                        setSelectedDiscountId('');
                        setDiscountKind('Promotion');
                        setDiscountCode('');
                        setDiscountDescription('');
                        setDiscountLineNo('');
                        setDiscountAmount('');
                      } else if (!selectedDiscountId && selectedDiscountRows[0]) {
                        setSelectedDiscountId(selectedDiscountRows[0].id);
                      }
                    }}
                  >
                    <option value="update">Correct existing discount</option>
                    <option value="add">Add missing discount</option>
                    <option value="delete">Remove incorrect discount</option>
                  </select>
                </Field>
                {discountOperation !== 'add' && (
                  <Field label="Discount entry">
                    <select
                      value={selectedDiscountId}
                      onChange={(event) => setSelectedDiscountId(event.target.value)}
                      disabled={selectedDiscountRows.length === 0}
                    >
                      {selectedDiscountRows.length === 0 && (
                        <option value="">No discount rows found</option>
                      )}
                      {selectedDiscountRows.map((row) => (
                        <option key={row.id} value={row.id}>
                          {row.kind} · {row.code || 'No code'} · {money(row.amount)}
                          {row.line_no ? ` · line ${row.line_no}` : ' · whole sale'}
                        </option>
                      ))}
                    </select>
                  </Field>
                )}
                {discountOperation === 'delete' ? (
                  <p className="hint">
                    This proposes removing{' '}
                    {selectedDiscountRows.find((row) => row.id === selectedDiscountId)?.kind ??
                      'the selected'}{' '}
                    discount. Finance approval is required.
                  </p>
                ) : (
                  <div className="cfg-form">
                    <Field label="Discount type">
                      <select
                        value={discountKind}
                        onChange={(event) => setDiscountKind(event.target.value)}
                      >
                        {['Promotion', 'Coupon', 'Voucher', 'Manual'].map((type) => (
                          <option key={type}>{type}</option>
                        ))}
                      </select>
                    </Field>
                    <Field label="Discount code">
                      <input
                        maxLength={100}
                        value={discountCode}
                        onChange={(event) => setDiscountCode(event.target.value)}
                      />
                    </Field>
                    <Field label="Description">
                      <input
                        maxLength={255}
                        value={discountDescription}
                        onChange={(event) => setDiscountDescription(event.target.value)}
                      />
                    </Field>
                    <Field label="Applies to">
                      <select
                        value={discountLineNo}
                        onChange={(event) => setDiscountLineNo(event.target.value)}
                      >
                        <option value="">Whole sale</option>
                        {selectedItemLines.map((line) => (
                          <option key={line.line_no} value={line.line_no}>
                            Line {line.line_no} · {line.item_code}
                            {line.description ? ` · ${line.description}` : ''}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <Field label="Corrected amount">
                      <input
                        type="number"
                        step="0.01"
                        value={discountAmount}
                        onChange={(event) => setDiscountAmount(event.target.value)}
                      />
                    </Field>
                  </div>
                )}
              </div>
            )}
            {['Tender', 'Reference'].includes(kind) && selectedTenderLines.length > 1 && (
              <Field label="Tender entry to correct">
                <select
                  value={selectedTenderId}
                  onChange={(e) => setSelectedTenderId(e.target.value)}
                >
                  <option value="">Select a tender entry</option>
                  {selectedTenderLines.map((line) => (
                    <option key={line.id} value={line.id}>
                      {line.tender_type} · {money(line.amount)}
                      {line.reference ? ` · ${line.reference}` : ''}
                    </option>
                  ))}
                </select>
              </Field>
            )}
            {kind === 'Tender' && (
              <Field label="Correct tender">
                <SearchSelect
                  label="Correct tender"
                  value={value}
                  onChange={setValue}
                  options={tenders.map((t) => ({ value: t }))}
                  placeholder="Search tenders"
                />
              </Field>
            )}
            {kind === 'Item' && (
              <div className="cfg-form">
                <Field label="Item line to correct">
                  <select
                    value={selectedItemLineNo}
                    onChange={(e) => setSelectedItemLineNo(e.target.value)}
                    disabled={selectedItemLines.length === 0}
                  >
                    {selectedItemLines.length === 0 && (
                      <option value="">No item lines found</option>
                    )}
                    {selectedItemLines.map((line) => (
                      <option key={line.line_no} value={line.line_no}>
                        Line {line.line_no} · {line.item_code}
                        {line.description ? ` · ${line.description}` : ''}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Replace with item">
                  <SearchSelect
                    label="Replace with item"
                    value={value}
                    onChange={setValue}
                    options={itemOptions}
                    onSearch={setItemQuery}
                    placeholder="Search items"
                    allowCustom
                  />
                </Field>
              </div>
            )}
            {kind === 'Customer' && (
              <div className="stack stack-sm">
                <p className="hint">
                  {picked
                    ? `Current customer: ${picked.customer_id || 'not recorded'}${picked.customer_identification_type ? ` · ${picked.customer_identification_type}` : ''}`
                    : 'Select the transaction from search to view its current masked customer details.'}
                </p>
                <Field label="Customer identifier">
                  <input
                    value={customerId}
                    onChange={(e) => setCustomerId(e.target.value)}
                    maxLength={100}
                    autoComplete="off"
                    placeholder="Enter the corrected identifier"
                  />
                </Field>
                <Field label="Identification type">
                  <input
                    value={customerIdentificationType}
                    onChange={(e) => setCustomerIdentificationType(e.target.value)}
                    maxLength={50}
                    placeholder="For example, Loyalty ID"
                  />
                </Field>
              </div>
            )}
            {kind === 'Employee' && (
              <div className="stack stack-sm">
                <p className="hint">
                  {picked
                    ? `Current cashier: ${picked.cashier_id || 'not recorded'} · salesperson: ${picked.salesperson_id || 'not recorded'}`
                    : 'Select the transaction from search to view its current employee details.'}{' '}
                  Enter any employee value to replace it, or choose clear. Leave other fields blank.
                </p>
                <Field label="Corrected cashier ID">
                  <input
                    value={clearCashier ? '' : cashierId}
                    onChange={(event) => {
                      setCashierId(event.target.value);
                      setClearCashier(false);
                    }}
                    maxLength={100}
                    autoComplete="off"
                    placeholder="Leave blank to keep current cashier"
                    disabled={clearCashier}
                  />
                </Field>
                <label className="check-row">
                  <input
                    type="checkbox"
                    checked={clearCashier}
                    onChange={(event) => {
                      setClearCashier(event.target.checked);
                      if (event.target.checked) setCashierId('');
                    }}
                  />
                  <span>Clear cashier ID</span>
                </label>
                <Field label="Corrected salesperson ID">
                  <input
                    value={clearSalesperson ? '' : salespersonId}
                    onChange={(event) => {
                      setSalespersonId(event.target.value);
                      setClearSalesperson(false);
                    }}
                    maxLength={100}
                    autoComplete="off"
                    placeholder="Leave blank to keep current salesperson"
                    disabled={clearSalesperson}
                  />
                </Field>
                <label className="check-row">
                  <input
                    type="checkbox"
                    checked={clearSalesperson}
                    onChange={(event) => {
                      setClearSalesperson(event.target.checked);
                      if (event.target.checked) setSalespersonId('');
                    }}
                  />
                  <span>Clear salesperson ID</span>
                </label>
              </div>
            )}
            {kind === 'CustomerAttribute' && (
              <div className="stack stack-sm">
                <p className="hint">
                  Customer attributes are retailer-defined. Use the exact attribute name from the
                  source system; approved values remain attached to this transaction.
                </p>
                <Field label="Attribute name">
                  <input
                    value={customerAttributeType}
                    onChange={(event) => setCustomerAttributeType(event.target.value)}
                    maxLength={64}
                    placeholder="For example, loyalty_tier"
                  />
                </Field>
                {picked && (
                  <p className="hint">
                    Current value:{' '}
                    {picked.customer_attributes?.[customerAttributeType] ?? 'not recorded'}
                    {Object.keys(picked.customer_attributes ?? {}).length > 0 && (
                      <span className="cluster" style={{ marginTop: '.4rem' }}>
                        {Object.entries(picked.customer_attributes ?? {}).map(([name, current]) => (
                          <Button
                            key={name}
                            size="sm"
                            variant="ghost"
                            onClick={() => {
                              setCustomerAttributeType(name);
                              setValue(current);
                              setClearCustomerAttribute(false);
                            }}
                          >
                            {name}
                          </Button>
                        ))}
                      </span>
                    )}
                  </p>
                )}
                <Field label="Attribute value">
                  <input
                    value={clearCustomerAttribute ? '' : value}
                    onChange={(event) => {
                      setValue(event.target.value);
                      setClearCustomerAttribute(false);
                    }}
                    maxLength={255}
                    placeholder="Enter the corrected value"
                    disabled={clearCustomerAttribute}
                  />
                </Field>
                <label className="check-row">
                  <input
                    type="checkbox"
                    checked={clearCustomerAttribute}
                    onChange={(event) => {
                      setClearCustomerAttribute(event.target.checked);
                      if (event.target.checked) setValue('');
                    }}
                  />
                  <span>Clear this attribute</span>
                </label>
              </div>
            )}
            {kind === 'UserReference' && (
              <div className="stack stack-sm">
                <Field label="Reference field">
                  <select
                    value={userReferenceField}
                    onChange={(event) => {
                      setUserReferenceField(event.target.value);
                      setValue('');
                      setClearUserReference(false);
                    }}
                  >
                    {[1, 2, 3, 4].map((number) => (
                      <option key={number} value={`reference_${number}`}>
                        Reference {number}
                      </option>
                    ))}
                  </select>
                </Field>
                <p className="hint">
                  {picked
                    ? `Current value: ${picked.user_references?.[userReferenceField] || 'not recorded'}`
                    : 'Select the transaction from search to view the current masked reference.'}
                </p>
                <Field label="Corrected reference value">
                  <input
                    value={clearUserReference ? '' : value}
                    onChange={(event) => {
                      setValue(event.target.value);
                      setClearUserReference(false);
                    }}
                    maxLength={255}
                    placeholder="Enter the corrected value"
                    disabled={clearUserReference}
                  />
                </Field>
                <label className="check-row">
                  <input
                    type="checkbox"
                    checked={clearUserReference}
                    onChange={(event) => {
                      setClearUserReference(event.target.checked);
                      if (event.target.checked) setValue('');
                    }}
                  />
                  <span>Clear this reference field</span>
                </label>
              </div>
            )}
            {kind === 'PaidOutDetail' && (
              <div className="stack stack-sm">
                <Field label="Paid-out field">
                  <select
                    value={paidOutField}
                    onChange={(event) => setPaidOutField(event.target.value)}
                  >
                    <option value="vendor_number">Vendor number</option>
                    <option value="vendor_invoice_number">Vendor invoice number</option>
                    <option value="proof_of_delivery_number">Proof of delivery number</option>
                    <option value="payment_reference_number">Payment reference number</option>
                  </select>
                </Field>
                <p className="hint">
                  Current value: {picked?.paid_out_details?.[paidOutField] || 'not recorded'}
                </p>
                <Field label="Corrected value">
                  <input
                    value={value}
                    onChange={(event) => setValue(event.target.value)}
                    maxLength={paidOutField === 'vendor_number' ? 10 : 30}
                  />
                </Field>
              </div>
            )}
            {kind === 'Reference' && (
              <div className="stack stack-sm">
                <Field label="Correct reference">
                  <input
                    value={value}
                    onChange={(e) => setValue(e.target.value)}
                    placeholder="Payment or settlement reference"
                  />
                </Field>
                {selectedTenderLines.length === 1 && (
                  <p className="hint">
                    This reference will update the transaction’s only tender entry.
                  </p>
                )}
                {selectedTenderLines.length === 0 && transactionId && (
                  <p className="hint">
                    No tender entry is recorded; this will update the transaction-level reference.
                  </p>
                )}
              </div>
            )}
            {missing && (
              <Card title="Optional receipt detail">
                <p className="hint">
                  Add the receipt lines and split tenders available from your evidence. Incomplete
                  detail is blocked; totals that disagree with the transaction are retained and
                  flagged for audit review. Without split tender rows, the tender selected above is
                  recorded for the full transaction amount.
                </p>
                {(manualLines.length > 0 ||
                  manualDiscounts.length > 0 ||
                  manualTenders.length > 0) && (
                  <div aria-live="polite">
                    <h3>Receipt preview</h3>
                    <dl className="kv">
                      {manualLines.length > 0 && (
                        <>
                          <dt>Items</dt>
                          <dd>{money(receiptPreview.gross)}</dd>
                          <dt>Tax</dt>
                          <dd>{money(receiptPreview.tax)}</dd>
                        </>
                      )}
                      {manualDiscounts.length > 0 && (
                        <>
                          <dt>Discounts</dt>
                          <dd>− {money(receiptPreview.discounts)}</dd>
                        </>
                      )}
                      {manualLines.length > 0 && (
                        <>
                          <dt>Expected total</dt>
                          <dd>{money(receiptPreview.expected)}</dd>
                          <dt>Difference from transaction</dt>
                          <dd>{money(receiptPreview.totalVariance)}</dd>
                        </>
                      )}
                      {manualTenders.length > 0 && (
                        <>
                          <dt>Tenders</dt>
                          <dd>{money(receiptPreview.tendered)}</dd>
                          <dt>Tender difference</dt>
                          <dd>{money(receiptPreview.tenderVariance)}</dd>
                        </>
                      )}
                    </dl>
                    {(manualLines.length > 0 && Math.abs(receiptPreview.totalVariance) > 0.01) ||
                    (manualTenders.length > 0 && Math.abs(receiptPreview.tenderVariance) > 0.01) ? (
                      <Alert tone="warning">
                        The receipt detail differs from the transaction amount. Finance approval
                        retains the evidence and marks the new transaction Unbalanced.
                      </Alert>
                    ) : null}
                  </div>
                )}
                <div className="section-heading">
                  <h3>Items</h3>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() =>
                      setManualLines((rows) => [
                        ...rows,
                        {
                          item_code: '',
                          description: '',
                          quantity: '1',
                          unit_price: '',
                          original_unit_price: '',
                          price_override_reason: '',
                          return_reason: '',
                          item_type: '',
                          item_status: '',
                          selling_uom: '',
                          uom_quantity: '',
                          line_salesperson_id: '',
                          swiped_or_scanned_at_pos: '',
                          taxable: '',
                          drop_ship: '',
                          pump: '',
                          recipient_state: '',
                          recipient_country: '',
                          reference_8: '',
                          customer_order_no: '',
                          customer_order_line_no: '',
                          customer_order_date: '',
                          media_id: '',
                          selling_item: '',
                          sales_type: '',
                          fulfillment_order_no: '',
                          return_warehouse: '',
                          return_without_inventory: '',
                          return_disposition: '',
                          tax_rate: '0',
                          tax_code: '',
                        },
                      ])
                    }
                  >
                    Add item
                  </Button>
                </div>
                {manualLines.map((line, index) => (
                  <div className="cfg-form" key={`line-${index}`}>
                    <Field label={`Item ${index + 1}`}>
                      <SearchSelect
                        label={`Item ${index + 1}`}
                        value={line.item_code}
                        placeholder="Search item or enter SKU"
                        options={itemOptions}
                        onSearch={setItemQuery}
                        allowCustom
                        onChange={(item_code) =>
                          setManualLines((rows) =>
                            rows.map((row, i) => (i === index ? { ...row, item_code } : row))
                          )
                        }
                      />
                    </Field>
                    <Field label="Description">
                      <input
                        value={line.description}
                        onChange={(e) =>
                          setManualLines((rows) =>
                            rows.map((row, i) =>
                              i === index ? { ...row, description: e.target.value } : row
                            )
                          )
                        }
                      />
                    </Field>
                    <details className="cfg-details">
                      <summary>Item status and POS attributes</summary>
                      <div className="cfg-form">
                        <Field label="Item type">
                          <input
                            maxLength={255}
                            value={line.item_type}
                            onChange={(e) =>
                              setManualLines((rows) =>
                                rows.map((row, i) =>
                                  i === index ? { ...row, item_type: e.target.value } : row
                                )
                              )
                            }
                          />
                        </Field>
                        <Field label="Item status">
                          <select
                            value={line.item_status}
                            onChange={(e) =>
                              setManualLines((rows) =>
                                rows.map((row, i) =>
                                  i === index ? { ...row, item_status: e.target.value } : row
                                )
                              )
                            }
                          >
                            <option value="">Not specified</option>
                            <option value="Sale">Sale</option>
                            <option value="Return">Return</option>
                          </select>
                        </Field>
                        {(
                          [
                            ['selling_uom', 'Selling UOM'],
                            ['line_salesperson_id', 'Salesperson ID'],
                            ['pump', 'Pump'],
                            ['recipient_state', 'Recipient state'],
                            ['recipient_country', 'Recipient country'],
                            ['reference_8', 'Reference 8'],
                          ] as const
                        ).map(([field, label]) => (
                          <Field label={label} key={field}>
                            <input
                              maxLength={255}
                              value={line[field]}
                              onChange={(e) =>
                                setManualLines((rows) =>
                                  rows.map((row, i) =>
                                    i === index ? { ...row, [field]: e.target.value } : row
                                  )
                                )
                              }
                            />
                          </Field>
                        ))}
                        <Field label="UOM quantity">
                          <input
                            type="number"
                            min="0.001"
                            step="0.001"
                            value={line.uom_quantity}
                            onChange={(e) =>
                              setManualLines((rows) =>
                                rows.map((row, i) =>
                                  i === index ? { ...row, uom_quantity: e.target.value } : row
                                )
                              )
                            }
                          />
                        </Field>
                        {(
                          [
                            ['swiped_or_scanned_at_pos', 'Swiped or scanned at POS'],
                            ['taxable', 'Taxable'],
                            ['drop_ship', 'Drop ship'],
                          ] as const
                        ).map(([field, label]) => (
                          <Field label={label} key={field}>
                            <select
                              value={line[field]}
                              onChange={(e) =>
                                setManualLines((rows) =>
                                  rows.map((row, i) =>
                                    i === index ? { ...row, [field]: e.target.value } : row
                                  )
                                )
                              }
                            >
                              <option value="">Not specified</option>
                              <option value="true">Yes</option>
                              <option value="false">No</option>
                            </select>
                          </Field>
                        ))}
                      </div>
                    </details>
                    <details className="cfg-details">
                      <summary>Customer order attributes</summary>
                      <div className="cfg-form">
                        {(
                          [
                            ['customer_order_no', 'Customer order number'],
                            ['customer_order_line_no', 'Customer order line number'],
                            ['media_id', 'Media ID'],
                            ['selling_item', 'Selling item'],
                            ['sales_type', 'Sales type'],
                            ['fulfillment_order_no', 'Fulfillment order number'],
                            ['return_warehouse', 'Return warehouse'],
                            ['return_disposition', 'Return disposition'],
                          ] as const
                        ).map(([field, label]) => (
                          <Field label={label} key={field}>
                            <input
                              maxLength={255}
                              value={line[field]}
                              onChange={(e) =>
                                setManualLines((rows) =>
                                  rows.map((row, i) =>
                                    i === index ? { ...row, [field]: e.target.value } : row
                                  )
                                )
                              }
                            />
                          </Field>
                        ))}
                        <Field label="Customer order date">
                          <input
                            type="date"
                            value={line.customer_order_date}
                            onChange={(e) =>
                              setManualLines((rows) =>
                                rows.map((row, i) =>
                                  i === index
                                    ? { ...row, customer_order_date: e.target.value }
                                    : row
                                )
                              )
                            }
                          />
                        </Field>
                        <Field label="Return without inventory">
                          <select
                            value={line.return_without_inventory}
                            onChange={(e) =>
                              setManualLines((rows) =>
                                rows.map((row, i) =>
                                  i === index
                                    ? { ...row, return_without_inventory: e.target.value }
                                    : row
                                )
                              )
                            }
                          >
                            <option value="">Not specified</option>
                            <option value="true">Yes</option>
                            <option value="false">No</option>
                          </select>
                        </Field>
                      </div>
                    </details>
                    <Field label="Qty">
                      <input
                        type="number"
                        min="0.001"
                        step="0.001"
                        value={line.quantity}
                        onChange={(e) =>
                          setManualLines((rows) =>
                            rows.map((row, i) =>
                              i === index ? { ...row, quantity: e.target.value } : row
                            )
                          )
                        }
                      />
                    </Field>
                    <Field label="Unit price">
                      <input
                        type="number"
                        min="0"
                        step="0.01"
                        value={line.unit_price}
                        onChange={(e) =>
                          setManualLines((rows) =>
                            rows.map((row, i) =>
                              i === index ? { ...row, unit_price: e.target.value } : row
                            )
                          )
                        }
                      />
                    </Field>
                    <Field
                      label="Original unit price"
                      hint="Optional; used when this item has a price override."
                    >
                      <input
                        type="number"
                        min="0"
                        step="0.01"
                        value={line.original_unit_price}
                        onChange={(e) =>
                          setManualLines((rows) =>
                            rows.map((row, i) =>
                              i === index ? { ...row, original_unit_price: e.target.value } : row
                            )
                          )
                        }
                      />
                    </Field>
                    <Field label="Price override reason">
                      <input
                        maxLength={255}
                        value={line.price_override_reason}
                        onChange={(e) =>
                          setManualLines((rows) =>
                            rows.map((row, i) =>
                              i === index ? { ...row, price_override_reason: e.target.value } : row
                            )
                          )
                        }
                      />
                    </Field>
                    {(['Return', 'Refund'].includes(transactionType) ||
                      line.item_status === 'Return') && (
                      <Field label="Return reason code">
                        <input
                          maxLength={255}
                          value={line.return_reason}
                          onChange={(e) =>
                            setManualLines((rows) =>
                              rows.map((row, i) =>
                                i === index ? { ...row, return_reason: e.target.value } : row
                              )
                            )
                          }
                        />
                      </Field>
                    )}
                    <Field label="Tax %">
                      <input
                        type="number"
                        min="0"
                        max="100"
                        step="0.01"
                        value={line.tax_rate}
                        onChange={(e) =>
                          setManualLines((rows) =>
                            rows.map((row, i) =>
                              i === index ? { ...row, tax_rate: e.target.value } : row
                            )
                          )
                        }
                      />
                    </Field>
                    <Field label="Tax code">
                      <input
                        value={line.tax_code}
                        onChange={(e) =>
                          setManualLines((rows) =>
                            rows.map((row, i) =>
                              i === index ? { ...row, tax_code: e.target.value } : row
                            )
                          )
                        }
                      />
                    </Field>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        setManualLines((rows) => rows.filter((_, i) => i !== index));
                        setManualDiscounts((rows) =>
                          rows.map((discount) => {
                            const lineNo = Number(discount.line_no);
                            if (!lineNo || !Number.isInteger(lineNo)) return discount;
                            if (lineNo === index + 1) return { ...discount, line_no: '' };
                            return lineNo > index + 1
                              ? { ...discount, line_no: String(lineNo - 1) }
                              : discount;
                          })
                        );
                      }}
                    >
                      Remove item
                    </Button>
                  </div>
                ))}
                <div className="section-heading">
                  <h3>Discounts</h3>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() =>
                      setManualDiscounts((rows) => [
                        ...rows,
                        { kind: 'Promotion', code: '', amount: '', line_no: '' },
                      ])
                    }
                  >
                    Add discount
                  </Button>
                </div>
                {manualDiscounts.map((discount, index) => (
                  <div className="cfg-form" key={`discount-${index}`}>
                    <Field label="Kind">
                      <select
                        value={discount.kind}
                        onChange={(e) =>
                          setManualDiscounts((rows) =>
                            rows.map((row, i) =>
                              i === index ? { ...row, kind: e.target.value } : row
                            )
                          )
                        }
                      >
                        {['Promotion', 'Coupon', 'Voucher', 'Manual'].map((kind) => (
                          <option key={kind}>{kind}</option>
                        ))}
                      </select>
                    </Field>
                    <Field label="Code">
                      <input
                        value={discount.code}
                        onChange={(e) =>
                          setManualDiscounts((rows) =>
                            rows.map((row, i) =>
                              i === index ? { ...row, code: e.target.value } : row
                            )
                          )
                        }
                      />
                    </Field>
                    <Field label="Applies to">
                      <select
                        value={discount.line_no}
                        onChange={(e) =>
                          setManualDiscounts((rows) =>
                            rows.map((row, i) =>
                              i === index ? { ...row, line_no: e.target.value } : row
                            )
                          )
                        }
                      >
                        <option value="">Whole sale</option>
                        {manualLines.map((line, lineIndex) => (
                          <option key={lineIndex} value={lineIndex + 1}>
                            Item {lineIndex + 1}
                            {line.item_code.trim() ? ` · ${line.item_code.trim()}` : ''}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <Field label="Amount">
                      <input
                        type="number"
                        min="0.01"
                        step="0.01"
                        value={discount.amount}
                        onChange={(e) =>
                          setManualDiscounts((rows) =>
                            rows.map((row, i) =>
                              i === index ? { ...row, amount: e.target.value } : row
                            )
                          )
                        }
                      />
                    </Field>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() =>
                        setManualDiscounts((rows) => rows.filter((_, i) => i !== index))
                      }
                    >
                      Remove discount
                    </Button>
                  </div>
                ))}
                <div className="section-heading">
                  <h3>Tenders</h3>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() =>
                      setManualTenders((rows) => [
                        ...rows,
                        { tender_type: tender, amount: '', reference: '', authorization: '' },
                      ])
                    }
                  >
                    Add tender
                  </Button>
                </div>
                {manualTenders.map((row, index) => (
                  <div className="cfg-form" key={`tender-${index}`}>
                    <Field label="Tender type">
                      <select
                        value={row.tender_type}
                        onChange={(e) =>
                          setManualTenders((rows) =>
                            rows.map((item, i) =>
                              i === index ? { ...item, tender_type: e.target.value } : item
                            )
                          )
                        }
                      >
                        {tenders.map((name) => (
                          <option key={name}>{name}</option>
                        ))}
                      </select>
                    </Field>
                    <Field label="Amount">
                      <input
                        type="number"
                        min="0.01"
                        step="0.01"
                        value={row.amount}
                        onChange={(e) =>
                          setManualTenders((rows) =>
                            rows.map((item, i) =>
                              i === index ? { ...item, amount: e.target.value } : item
                            )
                          )
                        }
                      />
                    </Field>
                    <Field label="Reference">
                      <input
                        value={row.reference}
                        onChange={(e) =>
                          setManualTenders((rows) =>
                            rows.map((item, i) =>
                              i === index ? { ...item, reference: e.target.value } : item
                            )
                          )
                        }
                      />
                    </Field>
                    <Field label="Authorization">
                      <input
                        value={row.authorization}
                        onChange={(e) =>
                          setManualTenders((rows) =>
                            rows.map((item, i) =>
                              i === index ? { ...item, authorization: e.target.value } : item
                            )
                          )
                        }
                      />
                    </Field>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setManualTenders((rows) => rows.filter((_, i) => i !== index))}
                    >
                      Remove tender
                    </Button>
                  </div>
                ))}
              </Card>
            )}
          </Card>

          <Card title="4. Why is this correction supported?">
            <Field
              label="Evidence and rationale"
              hint="Describe the receipt, source record, settlement report or other evidence you reviewed."
            >
              <textarea rows={4} value={rationale} onChange={(e) => setRationale(e.target.value)} />
            </Field>
            <div className="action-buttons">
              <Button variant="primary" disabled={!valid || busy} onClick={submit}>
                {busy ? 'Submitting…' : 'Submit for Finance review'}
              </Button>
              <Button onClick={() => router.push('/corrections')}>Cancel</Button>
            </div>
          </Card>
        </div>

        <aside className="corr-summary">
          <Card title="Your proposal">
            <Badge variant="secondary" size="sm">
              Source remains immutable
            </Badge>
            <dl className="kv">
              <dt>Type</dt>
              <dd>{pretty(kind)}</dd>
              <dt>Target</dt>
              <dd>
                {missing
                  ? 'New manual record'
                  : transactionId
                    ? picked
                      ? `${picked.store_id} · ${picked.business_date}`
                      : transactionId
                    : '—'}
              </dd>
              <dt>Effect</dt>
              <dd>{valid || missing || value || Number(amount) ? describe(kind, payload) : '—'}</dd>
              <dt>Why</dt>
              <dd>{rationale.trim() || '—'}</dd>
            </dl>
            <p className="hint">{active.help}</p>
            {missing && (
              <p className="hint">
                Approval creates an immutable manual source record and a linked canonical
                transaction.
              </p>
            )}
            <details className="cfg-details">
              <summary>Technical payload</summary>
              <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, margin: 0 }}>
                {JSON.stringify(payload, null, 2)}
              </pre>
            </details>
          </Card>
        </aside>
      </div>
    </div>
  );
}
