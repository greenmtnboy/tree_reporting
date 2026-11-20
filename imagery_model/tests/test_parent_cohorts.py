from types import SimpleNamespace

import pytest

from urban_tree_ml.warm_recipe import validate_parent_cohorts


def cohort(city, acquisition=None):
    return SimpleNamespace(inventory=SimpleNamespace(city=city),
                           imagery=SimpleNamespace(acquisition_id=acquisition))


def test_multiyear_parent_has_unique_cohorts():
    validate_parent_cohorts([cohort('USSFO'), cohort('USBOS'), cohort('USNYC'), cohort('USFFX'),
                            cohort('USBOS', 'usbos-naip-2021'), cohort('USBOS', 'usbos-naip-2025')])
    validate_parent_cohorts([cohort('USSFO'), cohort('USBOS')])


@pytest.mark.parametrize('extra', [cohort('USBOS'), cohort('USBOS','usnyc-naip-2025'), cohort('OTHER')])
def test_parent_rejects_duplicate_or_wrong_city_identity(extra):
    with pytest.raises(ValueError, match='unique city/acquisition'):
        validate_parent_cohorts([cohort('USSFO'), cohort('USBOS'), extra])
