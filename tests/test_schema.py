"""Pose schema tests: optional fields validate when present and when absent."""
from __future__ import annotations

import copy

import pytest
from jsonschema import Draft202012Validator

import validate
from common import POSES_DIR, load_schema, load_taxonomy, taxonomy_ids
from light_rules import light_groups

BASE_POSE = {
    "id": "01J0000000000000000000TEST",
    "slug": "credit-test-pose",
    "image": {"thumb": "thumb.jpg", "detail": "detail.jpg", "blurhash": "LKO2?U%2Tw=w"},
    "placeholder": False,
    "categories": ["couples"],
    "subject_count": 2,
    "subject_types": ["adult"],
    "light_conditions": ["golden"],
    "location_types": ["beach"],
    "orientation": "vertical",
    "difficulty": "easy",
    "image_source": "photo",
    "instructions": ["Angle the pair toward the sun."],
    "prompts": [
        {"text": "Breathe out.", "tone": "nervous_client"},
        {"text": "Race to that tree.", "tone": "playful"},
    ],
    "gear": {"focal_mm": [50, 85], "aperture": "f/1.8", "needs_reflector": False},
    "accessibility": [],
    "version": 1,
    "status": "active",
}


@pytest.fixture(scope="module")
def schema_validator():
    return Draft202012Validator(load_schema())


def schema_errors(validator, pose):
    return [e.message for e in validator.iter_errors(pose)]


def test_base_pose_is_schema_valid(schema_validator):
    assert schema_errors(schema_validator, BASE_POSE) == []


def test_photographer_credit_is_accepted_when_present(schema_validator):
    pose = copy.deepcopy(BASE_POSE)
    pose["photographer_credit"] = "Jane Doe Photography"
    assert schema_errors(schema_validator, pose) == []


def test_photographer_credit_is_optional(schema_validator):
    pose = copy.deepcopy(BASE_POSE)
    assert "photographer_credit" not in pose
    assert schema_errors(schema_validator, pose) == []


@pytest.mark.parametrize("bad", ["", "x" * 121, 42, None])
def test_photographer_credit_rejects_bad_values(schema_validator, bad):
    pose = copy.deepcopy(BASE_POSE)
    pose["photographer_credit"] = bad
    assert schema_errors(schema_validator, pose)


def test_validate_pose_passes_with_credit():
    """validate.validate_pose: the full check, images skipped, credit present.

    The pose dir is a repo-relative path that is never created: validate.rel()
    requires a path under the repo root, and with check_image_files=False
    nothing touches the filesystem.
    """
    pose = copy.deepcopy(BASE_POSE)
    pose["photographer_credit"] = "Jane Doe Photography"
    pose_dir = POSES_DIR / pose["id"]
    taxonomy = load_taxonomy()
    errors = validate.validate_pose(
        pose_dir, pose, Draft202012Validator(load_schema()),
        taxonomy_ids(taxonomy), light_groups(taxonomy), check_image_files=False,
    )
    assert errors == []
