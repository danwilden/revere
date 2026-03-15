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
