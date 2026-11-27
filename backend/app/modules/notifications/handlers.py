"""Event handlers of the notifications module (docs/M6-DESIGN.md §4.1, §5).

Empty in B49. B50 adds the handlers for deeds, feed, media, scoring and identity events and
B51 the ``MemberReviewUpdate`` handler; each writes through ``_write.notify``. Never subscribe
to ``FlagRaised``, ``FlagCaseResolved``, ``DeedHoldChanged``, ``FlagDecisionFinal``,
``HeroTitleRevoked`` or ``PointsChanged`` (D112; ``tests/test_architecture.py`` checks it).
"""
