"""Domain exceptions. Routers never catch these -- global FastAPI exception
handlers (registered in main.py) convert them to HTTP responses, so routers
stay free of try/except boilerplate."""


class NotFoundError(Exception):
    pass


class InvalidParameterError(Exception):
    pass
