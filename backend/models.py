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
    CashControl,
    CashDeposit,
)
from apps.controls.foundation.models import (
    FoundationReference,
)
from apps.controls.rules.models import (
    ConfiguredAuditRule,
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
from apps.store_days.models import (
    AuditTotal,
    StoreDay,
    StoreDayClosureRequest,
)
from apps.system.models import (
    Connector,
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
    "CashControl",
    "CashDeposit",
    "FoundationReference",
    "ConfiguredAuditRule",
    "TransactionAdjustment",
    "ExportBatch",
    "ExportDestination",
    "ExportItem",
    "TestSinkReceipt",
    "GLCrossReference",
    "ReconciliationMatch",
    "ReconciliationPolicy",
    "ShopifyReferenceStage",
    "ShopifyReferenceSyncState",
    "SourceFeedProfile",
    "SourceRecord",
    "SourceSetting",
    "BatchRun",
    "RetentionPolicy",
    "RetentionRun",
    "Connector",
    "AuditTotal",
    "StoreDay",
    "StoreDayClosureRequest",
    "AuditTotalDefinition",
    "SalesAuditErrorDefinition",
    "CanonicalTransaction",
    "TransactionDiscount",
    "TransactionLine",
    "TransactionTax",
    "TransactionTender",
    "AgentSetting",
]
