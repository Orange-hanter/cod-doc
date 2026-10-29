"""Foreign frontmatter vocabularies mapped onto cod-doc's enums.

Lives at the domain layer because two services read it: the importer bends a
foreign value into the enum through these tables, and drift
(``projection_service._frontmatter.metadata_mismatches``) has to recognise the
same spelling as agreeing with the row it produced. Two copies of the table
would drift apart; one module both import cannot.

Tables, not chains of ``if``s, so the mapping is greppable and testable. Every
substitution is still reported by the importer (``reason="alias"``) — a
documented rename is not a licence to stay silent.
"""

from __future__ import annotations

from typing import Final

from cod_doc.domain.entities import DocumentStatus, DocumentType

# Foreign status vocabularies met in imported corpora, mapped onto the
# cod-doc statuses (standards/frontmatter.md §2b).
ALIEN_STATUS_ALIASES: Final[dict[str, DocumentStatus]] = {
    # «живой» / завершённый документ чужого стандарта — работающий, не черновик
    "living": DocumentStatus.ACTIVE,
    "final": DocumentStatus.ACTIVE,
    "complete": DocumentStatus.ACTIVE,
    "completed": DocumentStatus.ACTIVE,
    # `resolved` и `done` здесь больше нет: с ADO-218 это собственные значения
    # DocumentStatus, а не написания `active`.
    "accepted": DocumentStatus.ACTIVE,
    "delivered": DocumentStatus.ACTIVE,
    "published": DocumentStatus.ACTIVE,
    "current": DocumentStatus.ACTIVE,
    "stable": DocumentStatus.ACTIVE,
    "in-progress": DocumentStatus.ACTIVE,
    "in_progress": DocumentStatus.ACTIVE,
    # ещё не принят
    "proposed": DocumentStatus.DRAFT,
    "pending": DocumentStatus.DRAFT,
    "wip": DocumentStatus.DRAFT,
    "todo": DocumentStatus.DRAFT,
    # на рассмотрении
    "in-review": DocumentStatus.REVIEW,
    "in_review": DocumentStatus.REVIEW,
    "reviewing": DocumentStatus.REVIEW,
    # снят с эксплуатации
    "archived": DocumentStatus.DEPRECATED,
    "superseded": DocumentStatus.DEPRECATED,
    "obsolete": DocumentStatus.DEPRECATED,
    "rejected": DocumentStatus.DEPRECATED,
    "cancelled": DocumentStatus.DEPRECATED,
}

# Foreign `type:` values of corpora that validate their own frontmatter and
# cannot rewrite it into cod-doc's vocabulary (standards/frontmatter.md §2c).
# First met in ZeusLogic, whose `tools/docs_check.py` accepts exactly
# `requirements | architecture | specification | design | convention | adr`;
# `architecture` and `design` are cod-doc values already.
ALIEN_TYPE_ALIASES: Final[dict[str, DocumentType]] = {
    "specification": DocumentType.MODULE_SPEC,
    "spec": DocumentType.MODULE_SPEC,
    "convention": DocumentType.STANDARD,
    "requirements": DocumentType.VISION,
    "adr": DocumentType.DECISION,
}
