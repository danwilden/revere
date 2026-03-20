from enum import Enum


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobType(str, Enum):
    INGESTION = "ingestion"
    HMM_TRAINING = "hmm_training"
    BACKTEST = "backtest"
    FEATURE_GENERATION = "feature_generation"
    SIGNAL_MATERIALIZE = "signal_materialize"
    DUKASCOPY_DOWNLOAD = "dukascopy_download"
    RESEARCH = "RESEARCH"
    FEATURE_DISCOVERY = "FEATURE_DISCOVERY"    # Phase 5C agent-driven discovery
    AUTOML_TRAIN = "automl_train"              # Phase 5D SageMaker Autopilot
    ROBUSTNESS_BATTERY = "robustness_battery"  # Phase 5F validation battery


class ExperimentStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"
    VALIDATED = "validated"   # Phase 5F — approved after robustness battery
    DISCARDED = "discarded"   # Phase 5F — explicitly rejected


class Timeframe(str, Enum):
    M1 = "M1"
    H1 = "H1"
    H4 = "H4"
    D = "D"


class StrategyType(str, Enum):
    PYTHON = "python"
    RULES_ENGINE = "rules_engine"
    HYBRID = "hybrid"


class SignalType(str, Enum):
    HMM_REGIME = "hmm_regime"
    CODE = "code"
    DECLARATIVE = "declarative"
    AUTOML_DIRECTION_PROB = "automl_direction_prob"   # Phase 5D
    AUTOML_RETURN_BUCKET = "automl_return_bucket"     # Phase 5D
    HMM_STATE_PROB = "hmm_state_prob"                 # Phase 5E
    RISK_FILTER = "risk_filter"                       # Phase 5E


class DataSource(str, Enum):
    OANDA = "oanda"
    DUKASCOPY = "dukascopy"


class InstrumentCategory(str, Enum):
    MAJOR = "major"
    MINOR = "minor"
    EXOTIC = "exotic"


class TradeSide(str, Enum):
    LONG = "long"
    SHORT = "short"


class QualityFlag(str, Enum):
    OK = "ok"
    GAP = "gap"
    DUPLICATE = "duplicate"
    OHLC_INVALID = "ohlc_invalid"
    ESTIMATED = "estimated"
