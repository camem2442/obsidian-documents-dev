"""Canonical data model package for Exam Core."""
from .schema import (
    SCHEMA_VERSION,
    SOURCE_AUDIT_RESULTS,
    SOURCE_AUDIT_VALUES,
    AssetDomain,
    CanonicalQuestionRecord,
    ClassificationCandidate,
    ClassificationDomain,
    OriginDomain,
    ProvenanceDomain,
    QuestionDomain,
    RelationsDomain,
    ReviewDomain,
    SourceDomain,
    SourceLocator,
    SourceRegion,
    UnitDomain,
)
from .validate import validate_canonical_dict
from .relations import canonical_relation_errors

__all__ = [
    'SCHEMA_VERSION',
    'SOURCE_AUDIT_RESULTS',
    'SOURCE_AUDIT_VALUES',
    'AssetDomain',
    'CanonicalQuestionRecord',
    'ClassificationCandidate',
    'ClassificationDomain',
    'OriginDomain',
    'ProvenanceDomain',
    'QuestionDomain',
    'RelationsDomain',
    'ReviewDomain',
    'SourceDomain',
    'SourceLocator',
    'SourceRegion',
    'UnitDomain',
    'validate_canonical_dict',
    'canonical_relation_errors',
]
