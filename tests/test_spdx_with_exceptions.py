from __future__ import annotations

from hungry_crab.licensing import LicenseClass, MawClass, Mode, classify, decide_for_class, normalize
from hungry_crab.licensing.matrix import fits_gpl_maw, governing_id


def test_supported_with_exception_keeps_base_license_semantics() -> None:
    prey = "GPL-2.0-only with classpath-exception-2.0"
    normalized = "GPL-2.0-only WITH Classpath-exception-2.0"

    assert normalize(prey) == normalized
    assert classify(prey) is LicenseClass.GPL
    assert governing_id(prey) == normalized
    assert fits_gpl_maw(prey, "GPL-2.0-only")
    assert decide_for_class(prey, MawClass.GPL, "GPL-2.0-only").mode is Mode.COPY


def test_unknown_with_exception_fails_closed_across_all_entry_points() -> None:
    prey = "GPL-2.0-only WITH Totally-Fake-Exception"

    assert normalize(prey) == prey
    assert classify(prey) is LicenseClass.UNKNOWN
    assert governing_id(prey) == prey
    assert not fits_gpl_maw(prey, "GPL-2.0-only")
    verdict = decide_for_class(prey, MawClass.GPL, "GPL-2.0-only")
    assert verdict.mode is Mode.HUMAN
    assert verdict.human_review


def test_multiple_with_operators_fail_closed() -> None:
    prey = "GPL-2.0-only WITH Classpath-exception-2.0 WITH LLVM-exception"

    assert classify(prey) is LicenseClass.UNKNOWN
    assert not fits_gpl_maw(prey, "GPL-2.0-only")
    verdict = decide_for_class(prey, MawClass.GPL, "GPL-2.0-only")
    assert verdict.mode is Mode.HUMAN
    assert verdict.human_review


def test_with_on_a_compound_expression_fails_closed() -> None:
    prey = "(GPL-2.0-only OR MIT) WITH Classpath-exception-2.0"

    assert classify(prey) is LicenseClass.UNKNOWN
    assert not fits_gpl_maw(prey, "GPL-2.0-only")
    verdict = decide_for_class(prey, MawClass.GPL, "GPL-2.0-only")
    assert verdict.mode is Mode.HUMAN
    assert verdict.human_review
