"""Validate fixed-size TZA hybrid recipes without changing license definitions.

Gate equivalence here is an orthogonal-layout proof, not a substitute for native
purchase restrictions, ownership, LP or special-license behavior.
"""
import hashlib
import struct

EMPTY = 0xffff
SPECIAL = frozenset(range(31))  # Existing Quickening and Esper license IDs.


def board_cells(raw):
    if len(raw) != 1160 or raw[:8] != b'licd\x18\x00\x18\x00':
        raise ValueError('expected a fixed 24x24 licd board')
    ids = struct.unpack_from('<576H', raw, 8)
    if any(value > 360 and value != EMPTY for value in ids):
        raise ValueError('unsupported license ID')
    return {(i % 24, i // 24): value for i, value in enumerate(ids) if value != EMPTY}


def graph(cells):
    edges = set()
    for (x, y), value in cells.items():
        for point in ((x+1, y), (x, y+1)):
            other = cells.get(point)
            if other is not None and other != value:
                edges.add(tuple(sorted((value, other))))
    return edges


def gate_contract(sources):
    """Contract connected ordinary licenses, retaining each special license."""
    ids = set().union(*(set(cells.values()) for cells in sources))
    edges = set().union(*(graph(cells) for cells in sources))
    parent = {value: value for value in ids - SPECIAL}
    def find(value):
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value
    for a, b in edges:
        if a in parent and b in parent:
            x, y = find(a), find(b)
            parent[max(x, y)] = min(x, y)
    labels = {value: f'ordinary:{find(value)}' if value in parent else f'gate:{value}'
              for value in ids}
    projected = {tuple(sorted((labels[a], labels[b]))) for a, b in edges if labels[a] != labels[b]}
    return labels, projected


def validate_recipe(recipe, sources, license_table):
    """Return an authored board only if source pins and gate graph agree.

    Sources maps native job IDs to original board bytes. The caller must choose
    their authoritative source/winner; this validator does not access a game.
    """
    if recipe.get('schemaVersion') != 1 or recipe.get('width') != 24 or recipe.get('height') != 24:
        raise ValueError('unsupported recipe schema or geometry')
    pair = recipe.get('pair')
    if not isinstance(pair, list) or len(pair) != 2 or any(type(v) is not int or not 0 <= v < 12 for v in pair):
        raise ValueError('invalid native pair')
    if pair != sorted(set(pair)) or set(sources) != set(pair):
        raise ValueError('pair must be canonical and match both sources')
    pins = recipe.get('sourceBoards')
    expected = [{'job': job, 'sha256': hashlib.sha256(sources[job]).hexdigest()} for job in pair]
    if pins != expected or recipe.get('licenseTableSha256') != hashlib.sha256(license_table).hexdigest():
        raise ValueError('source board or license-table pin mismatch')
    source_cells = [board_cells(sources[job]) for job in pair]
    required = set().union(*(set(cells.values()) for cells in source_cells))
    cells, seen = {}, set()
    nodes = recipe.get('cells')
    if not isinstance(nodes, list) or not nodes:
        raise ValueError('empty hybrid recipe')
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError('invalid node')
        x, y, value = (node.get(key) for key in ('x', 'y', 'licenseId'))
        if any(type(v) is not int for v in (x, y, value)) or not 0 <= x < 24 or not 0 <= y < 24:
            raise ValueError('invalid node coordinate or ID')
        if (x, y) in cells or value in seen or value not in required:
            raise ValueError('duplicate coordinate/license or license outside source curriculum')
        if any(not isinstance(node.get(key), str) or not node[key].strip() for key in ('branch', 'purpose')):
            raise ValueError('authored branch and purpose required')
        cells[x, y] = value
        seen.add(value)
    if seen != required:
        raise ValueError('source licenses missing from hybrid')
    labels, expected_edges = gate_contract(source_cells)
    actual_graph = graph(cells)
    actual_edges = {tuple(sorted((labels[a], labels[b]))) for a, b in actual_graph if labels[a] != labels[b]}
    if actual_edges != expected_edges:
        raise ValueError('special-gate topology differs from source pair')
    # Every contracted ordinary component must also remain connected internally.
    adjacency = {value: set() for value in seen}
    for a, b in actual_graph:
        if labels[a] == labels[b]:
            adjacency[a].add(b)
            adjacency[b].add(a)
    for label in set(labels.values()):
        if not label.startswith('ordinary:'):
            continue
        component = {value for value in seen if labels[value] == label}
        reached, todo = set(), [next(iter(component))]
        while todo:
            value = todo.pop()
            if value not in reached:
                reached.add(value)
                todo.extend(adjacency[value] - reached)
        if reached != component:
            raise ValueError('ordinary source component disconnected')
    ids = [EMPTY]*576
    for (x, y), value in cells.items():
        ids[y*24+x] = value
    return b'licd'+struct.pack('<HH576H', 24, 24, *ids)
