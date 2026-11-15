"""Mehrstufiges Agentensystem.

Komponenten und ihre Module:

* Task Reader        -> ``app.agent.task_reader``
* Task Analyzer      -> ``app.agent.analyzer`` (+ ``app.tasks.classifier``)
* Database Inspector -> ``app.access.inspector``
* Solution Planner   -> ``app.agent.planner`` (Planvalidierung, SQL-Auflösung)
* Access Executor    -> ``app.agent.executor``
* UI Executor        -> bewusst nicht enthalten (keine Maus-/Tastatur-Fernsteuerung; alles läuft über COM)
* Result Verifier    -> ``app.agent.verifier``
* Recovery Manager   -> ``app.agent.recovery``
* Explanation Engine -> ``app.agent.explainer``

``app.agent.orchestrator.TaskPipeline`` verbindet sie zum Gesamtablauf.
"""
