"""In-app notifications (M6A, docs/M6-DESIGN.md §2.1, D103 to D115): one row per recipient,
written by event handlers, holding ids and numbers only; the text is built when the list is read.
May import ``app.core``, ``identity.service``, ``deeds.service`` and, from ``media``, ``scoring``,
``feed`` and ``fraud``, only the names allowed in D104. Nobody imports ``notifications``.
"""
