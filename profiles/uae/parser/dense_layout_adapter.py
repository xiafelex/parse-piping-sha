"""UAE-only least-constraining-value ordering for Chengda's exact solver.

The existing compatibility rules and final zero-conflict requirements are kept.
Only the candidate visitation order changes; the shared source/module is never
modified. Guard the adaptation so upstream changes cannot silently misapply it.
"""
import inspect,math


def solve_dense(engine,points,options):
    source=inspect.getsource(engine.solve_page_layout)
    marker='        values = [candidate for candidate in range(len(options_by_point[variable])) if domain & (1 << candidate)]'
    if source.count(marker)!=1:raise RuntimeError('Chengda solver changed; review UAE dense-page adapter')
    ordering='''
        # Preserve the most remaining choices for the unassigned neighbours.
        remaining=[other for other in range(count) if other!=variable and assigned[other]<0]
        values.sort(key=lambda candidate:(
            -sum((current_domains[other] & compatible[variable][candidate][other]).bit_count() for other in remaining),
            options_by_point[variable][candidate].placement.score))'''
    source=source.replace(marker,marker+ordering)
    scope=dict(vars(engine));scope['AVOIDABLE_SOURCE_CROSSING_PENALTY']=math.inf
    exec(compile(source,'<uae_dense_layout_adapter>','exec'),scope)
    return scope['solve_page_layout'](points,options,node_limit=60000,allow_conflicts=False)
