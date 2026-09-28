from collections import Counter


def semantic_shape(texts):
    lens = [len(str(x)) for x in texts]
    tokens = Counter(w.lower() for x in texts for w in str(x).split())
    return {
        "count": len(lens),
        "mean_length": sum(lens) / len(lens) if lens else 0,
        "vocabulary": len(tokens),
        "top_tokens": tokens.most_common(20),
    }


def template_generate(template, rows):
    return [template.format(**r) for r in rows]
