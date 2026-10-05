"""Pipeline components, loaded on demand to keep format registration acyclic."""

_EXPORTS = {
    "process": (".ai", "process"),
    "symbol_differences": (".ai", "symbol_differences"),
    "WorkQueue": (".work_queue", "WorkQueue"),
    "new_structure": (".workbook", "new_structure"),
    "read_structure": (".workbook", "read_structure"),
    "confirm_span": (".workbook", "confirm_span"),
    "verify_sources": (".workbook", "verify_sources"),
    "structure_report": (".workbook", "structure_report"),
    "split_pdfs": (".workbook", "split_pdfs"),
}


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    from importlib import import_module
    module_name, attribute = _EXPORTS[name]
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value


__all__ = list(_EXPORTS)
