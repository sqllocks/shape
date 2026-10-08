"""The education domain: departments, courses, instructors, students, sections, enrollments,
academic standing, financial aid and grade appeals (9 tables), with the reference data it draws
from (aid types, course catalog, department names)."""

from __future__ import annotations

from shape_domains._packaged import PackagedDomain

SHAPE_API = "1.0"


class EducationDomain(PackagedDomain):
    """``shape.domains`` entry ``education``."""

    name = "education"
    description = "Education domain with students, courses, enrollments, grades, and financial aid"
    datasets = ("aid_types", "course_catalog", "department_names")
