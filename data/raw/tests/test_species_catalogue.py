"""Genus totals cover the full inventory; trunk photos retain their own credit."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'website'))
from species_catalogue import _photo, rank_genera


class SpeciesContextTest(unittest.TestCase):
    def test_genus_totals_include_genus_only_and_unenriched_entries(self):
        ranks = rank_genera(
            [('Quercus', 20), ('Quercus robur', 40), ('Quercus rare', 1),
             ('Acer rubrum', 60), ('Legacy name', 2)],
            {'Legacy name': {'genus': 'Quercus'}},
        )
        self.assertEqual(ranks['Quercus'], {'rank': 1, 'trees': 63})
        self.assertEqual(ranks['Acer'], {'rank': 2, 'trees': 60})

    def test_equal_genus_totals_share_a_rank(self):
        ranks = rank_genera([('Acer', 10), ('Quercus', 10), ('Ulmus', 5)], {})
        self.assertEqual([ranks[g]['rank'] for g in ['Acer', 'Quercus', 'Ulmus']], [1, 1, 3])

    def test_trunk_photo_uses_its_own_url_license_and_credit(self):
        photo = _photo({
            'photo_attribution': 'Overview author',
            'trunk_photo_url': 'https://inaturalist-open-data.s3.amazonaws.com/photos/2305598/medium.JPG',
            'trunk_photo_license': 'cc-by-nc',
            'trunk_photo_attribution': 'Trunk author',
        }, 'trunk_photo')
        self.assertEqual(photo['attribution'], 'Trunk author')
        self.assertEqual(photo['license'], 'cc-by-nc')
        self.assertTrue(photo['thumb'].endswith('/square.JPG'))

    def test_missing_or_broken_trunk_photo_does_not_borrow_overview(self):
        self.assertIsNone(_photo({'photo_url': 'https://inaturalist-open-data.s3.amazonaws.com/photos/1/medium.jpg'}, 'trunk_photo'))
        self.assertIsNone(_photo({'trunk_photo_url': 'https://inaturalist-open-data.s3.amazonaws.com/photos/1/medium'}, 'trunk_photo'))


if __name__ == '__main__':
    unittest.main()
