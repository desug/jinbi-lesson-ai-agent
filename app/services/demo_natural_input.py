"""Closed vocabulary for a future fictional-only natural-language DEMO.

This is not a general sensitive-data detector and is not yet wired to a route.
Unknown text is rejected. Authorization and write checks remain separate.
"""
# Only the names and ordinary query vocabulary of this isolated demo are
# admitted. Extend deliberately with tests, never from browser input.
_WORDS = (
    '虚构小明', '数学', '英语', '语文', '物理', '化学', '生物', '历史', '地理', '政治',
    '帮我', '看看', '查一下', '查询', '查看', '还剩', '多少', '还有没有', '剩余',
    '课时', '课程', '课', '有多少', '有没有', '还有', '剩下', '学生', '学员',
    '请', '我', '的', '是', '吗', '呢', '什么', '多少', '？', '?', '。', '！', '!', '，', ',', ' ',
)
_TOKENS = tuple(sorted(set(_WORDS), key=len, reverse=True))

def require_fictional_query(query):
    """Return unchanged input only when every token is explicitly allowed."""
    if not isinstance(query, str) or not query.strip() or len(query) > 200:
        raise ValueError('输入超出虚构演示允许范围')
    offset = 0
    while offset < len(query):
        token = next((word for word in _TOKENS if query.startswith(word, offset)), None)
        if token is None:
            raise ValueError('输入超出虚构演示允许范围')
        offset += len(token)
    return query
