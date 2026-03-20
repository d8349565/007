from app.services.query import get_graph_data
data = get_graph_data()
print(f"nodes: {len(data['nodes'])}, edges: {len(data['edges'])}")
print(f"stats: {data['stats']}")
# 显示前3个节点
for n in data['nodes'][:3]:
    print(f"  node: {n['name']} is_text={n.get('is_text_node')} fact_count={n['fact_count']}")
# 显示前3条边
for e in data['edges'][:3]:
    print(f"  edge: {e['subject_text']} --{e['predicate']}--> {e['object_text']}")
