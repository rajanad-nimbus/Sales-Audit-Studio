"""Compatibility imports for SQLAlchemy models owned by backend feature apps.

Alembic imports this module to register every table on the shared declarative
base. Application code should import models from the feature that owns them.
"""

from apps.agents.models import (
    AgentSetting,
)
from apps.audit.models import (
    AuditEvent,
)
from apps.cases.models import (
    Case,
    CaseNote,
    EvidenceRequest,
    EvidenceSnapshot,
    Exception,
    Finding,
    HumanDecision,
    PolicyEvaluation,
    Recommendation,
    ValidationObligation,
    Workflow,
)
from apps.cash.models import (
    ACHAdjustment,
    CashControl,
    CashDeposit,
)
from apps.controls.foundation.models import (
    FoundationImportRun,
    FoundationReference,
    StoreLocationTrait,
)
from apps.controls.rules.models import (
    ConfiguredAuditRule,
)
from apps.controls.rules.transaction_models import (
    ConfiguredTransactionAuditRule,
)
from apps.corrections.models import (
    TransactionAdjustment,
)
from apps.exports.models import (
    ExportBatch,
    ExportDestination,
    ExportItem,
    TestSinkReceipt,
)
from apps.gl.models import (
    GLCrossReference,
)
from apps.ingest.models import (
    ReconciliationMatch,
    ReconciliationPolicy,
    RegisterEvent,
    RegisterEventAdjustment,
    ShopifyReferenceStage,
    ShopifyReferenceSyncState,
    SourceFeedProfile,
    SourceRecord,
    SourceSetting,
)
from apps.operations.models import (
    BatchRun,
    RetentionPolicy,
    RetentionRun,
)
from apps.security.models import StoreAssignment, UserPreference, UserSavedViews, VerifiedStoreUser
from apps.store_days.models import (
    AuditTotal,
    PeriodClose,
    StoreDay,
    StoreDayClosureRequest,
    VarianceExplanation,
    VarianceTolerance,
)
from apps.system.models import (
    Connector,
    SystemOption,
)
from apps.totals.models import (
    AuditTotalDefinition,
    SalesAuditErrorDefinition,
)
from apps.transactions.models import (
    CanonicalTransaction,
    TransactionDiscount,
    TransactionLine,
    TransactionTax,
    TransactionTender,
)
from apps.vouchers.models import (
    Voucher,
    VoucherIdentity,
    VoucherOption,
    VoucherRoutingPolicy,
)
from model_base import Base, utcnow

__all__ = [
    "Base",
    "utcnow",
    "AuditEvent",
    "Case",
    "CaseNote",
    "EvidenceRequest",
    "EvidenceSnapshot",
    "Exception",
    "Finding",
    "HumanDecision",
    "PolicyEvaluation",
    "Recommendation",
    "ValidationObligation",
    "Workflow",
    "ACHAdjustment",
    "CashControl",
    "CashDeposit",
    "FoundationReference",
    "StoreLocationTrait",
    "FoundationImportRun",
    "ConfiguredAuditRule",
    "ConfiguredTransactionAuditRule",
    "TransactionAdjustment",
    "ExportBatch",
    "ExportDestination",
    "ExportItem",
    "TestSinkReceipt",
    "GLCrossReference",
    "ReconciliationMatch",
    "ReconciliationPolicy",
    "RegisterEvent",
    "RegisterEventAdjustment",
    "ShopifyReferenceStage",
    "ShopifyReferenceSyncState",
    "SourceFeedProfile",
    "SourceRecord",
    "SourceSetting",
    "BatchRun",
    "RetentionPolicy",
    "RetentionRun",
    "StoreAssignment",
    "UserPreference",
    "UserSavedViews",
    "VerifiedStoreUser",
    "Connector",
    "SystemOption",
    "AuditTotal",
    "StoreDay",
    "StoreDayClosureRequest",
    "PeriodClose",
    "Voucher",
    "VoucherOption",
    "VoucherIdentity",
    "VoucherRoutingPolicy",
    "VarianceExplanation",
    "VarianceTolerance",
    "AuditTotalDefinition",
    "SalesAuditErrorDefinition",
    "CanonicalTransaction",
    "TransactionDiscount",
    "TransactionLine",
    "TransactionTax",
    "TransactionTender",
    "AgentSetting",
]
