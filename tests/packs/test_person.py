from shape.packs import generate_person


def test_person_deterministic_coherent():
    a = generate_person(4, 9)
    b = generate_person(4, 9)
    assert (
        a == b
        and a["full_name"] == a["first_name"] + " " + a["last_name"]
        and a["email"].endswith("@example.invalid")
    )


def test_person_seeds_of_opposite_sign_are_different_streams():
    """#381: random.Random used |seed|, so seed -s and seed s gave the same person at row 0."""
    positive = [generate_person(0, s) for s in range(1, 31)]
    negative = [generate_person(0, -s) for s in range(1, 31)]
    assert positive != negative
