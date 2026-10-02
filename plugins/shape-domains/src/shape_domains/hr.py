"""The hr domain: departments, positions, employees, compensation, performance reviews, time-off
requests, training, training enrollments and terminations (9 tables), with the reference data it draws from (department names, position
titles and training courses)."""

from __future__ import annotations

from shape_domains._packaged import PackagedDomain

SHAPE_API = "1.0"


class HrDomain(PackagedDomain):
    """``shape.domains`` entry ``hr``."""

    name = "hr"
    datasets = ("department_names", "position_titles", "training_courses")
