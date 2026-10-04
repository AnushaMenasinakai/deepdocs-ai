"""Structured offline provider fixture; references only supplied pages."""
def claims_for(text):
    async def answer(question, context):
        return [{"text": text, "citation_ids": [s.citation_id for s in context.sources]}]
    return answer
