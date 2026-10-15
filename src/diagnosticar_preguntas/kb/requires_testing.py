"""HPO subtrees whose findings a family cannot observe at home.

A term requires testing when it or one of its ancestors is listed here. The
roots are grouped by the kind of study that reveals them. They are chosen as
high as possible without swallowing observable signs: for example
``Abnormal bleeding`` stays outside the haematology roots, and ``Scoliosis``
or ``Macrocephaly`` stay outside the radiology roots, because a parent can
notice them. The list is deliberately conservative in the other direction
too: a finding a doctor usually names to the family (a heart defect on an
echocardiogram, anaemia on a blood count) still counts as requiring a study,
since the question then becomes "did a doctor tell you...".

HPO terms can have several parents, so a sign a family does see may also sit
under a study root: "Microcephaly" is both a skull-size finding and a brain
morphology one, and "Fever" is filed under homeostasis. ``OBSERVABLE_ROOTS``
lists such subtrees; a term under one of them never requires testing, since
at least one of its meanings is observable.
"""

from __future__ import annotations

from types import MappingProxyType

_ROOTS: dict[str, dict[str, str]] = {
    "laboratory": {
        "HP:0001939": "Abnormality of metabolism/homeostasis",
        "HP:0003117": "Abnormal circulating hormone concentration",
        "HP:0010701": "Abnormal circulating immunoglobulin concentration",
        "HP:0011111": "Abnormal immune serum protein physiology",
        "HP:0001881": "Abnormal leukocyte morphology",
        "HP:0033796": "Abnormal leukocyte physiology",
        "HP:0001877": "Abnormal erythrocyte morphology",
        "HP:0001872": "Abnormality of thrombocytes",
        "HP:0001928": "Abnormality of coagulation",
        "HP:0005561": "Abnormal bone marrow cell morphology",
        "HP:0012211": "Abnormal renal physiology",
    },
    "histology": {
        "HP:0025354": "Abnormal cellular phenotype",
        "HP:0011792": "Neoplasm by histology",
    },
    "electrophysiology": {
        "HP:0001311": "Abnormal nervous system electrophysiology",
        "HP:0003115": "Abnormal EKG",
        "HP:0500015": "Abnormal cardiac test",
    },
    "imaging": {
        "HP:0002011": "Morphological central nervous system abnormality",
        "HP:0410263": "Brain imaging abnormality",
        "HP:0012447": "Abnormal myelination",
        "HP:0001627": "Abnormal heart morphology",
        "HP:0012210": "Abnormal renal morphology",
        "HP:0031983": "Abnormal pulmonary thoracic imaging finding",
        "HP:0003330": "Abnormal bone structure",
        "HP:0005930": "Abnormal epiphysis morphology",
        "HP:0000944": "Abnormal metaphysis morphology",
        "HP:0003468": "Abnormal vertebral morphology",
    },
    "specialist_examination": {
        "HP:0001098": "Abnormal fundus morphology",
    },
}

REQUIRES_TESTING_ROOTS: MappingProxyType[str, str] = MappingProxyType(
    {hpo_id: label for group in _ROOTS.values() for hpo_id, label in group.items()}
)

OBSERVABLE_ROOTS: MappingProxyType[str, str] = MappingProxyType(
    {
        "HP:0000240": "Abnormality of skull size",
        "HP:0004370": "Abnormality of temperature regulation",
        "HP:0000969": "Edema",
        "HP:0001944": "Dehydration",
        "HP:0001297": "Stroke",
    }
)
