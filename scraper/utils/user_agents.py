from fake_useragent import UserAgent

_ua = UserAgent()


def random_user_agent() -> str:
    return _ua.random
