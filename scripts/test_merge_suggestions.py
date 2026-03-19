"""验证合并建议"""
import sys
sys.path.insert(0, '.')
from app.services.entity_merger import get_merge_suggestions
s = get_merge_suggestions()
print(f"共 {len(s)} 条合并建议，前15条:")
for item in s[:15]:
    print(f"  [{item['score']:.2f}] \"{item['secondary_name']}\" -> \"{item['primary_name']}\"  ({item['reason']})")
