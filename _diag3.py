from app.services.entity_linker import ai_suggest_relations
print("ai_suggest_relations imported OK")

from app.web.review_app import create_app
app = create_app()
routes = [r.rule for r in app.url_map.iter_rules() if 'ai-suggest' in r.rule]
print("AI routes:", routes)
