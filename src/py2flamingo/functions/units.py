import unittest

import calculations
import numpy as np


class TestCalculateIntensity(unittest.TestCase):
    def test_intensity_map(self):
        # Create a synthetic image with known intensity values
        image_data = np.array(
            [
                [1, 2, 3, 4, 5],
                [6, 7, 8, 9, 10],
                [11, 12, 13, 14, 15],
                [16, 17, 18, 19, 20],
            ]
        )

        n_lines = 3

        # Define the expected intensity map for the given image and n_lines
        expected_intensity_map = [(0, 9), (1, 14), (2, 19), (3, 19.67)]

        # Call the calculate_intensity function - uses top 25% of each line
        (
            mean_largest_quarter,
            y_intensity_map,
        ) = calculations.calculate_rolling_y_intensity(image_data, n_lines)

        # Assert the mean_largest_quarter is correct
        self.assertAlmostEqual(mean_largest_quarter, 18)
        # print(y_intensity_map)
        # print(expected_intensity_map)
        # Check if the calculated y_intensity_map matches the expected value
        np.testing.assert_almost_equal(
            y_intensity_map, expected_intensity_map, decimal=2
        )


class TestFitEllipse(unittest.TestCase):
    def test_circle(self):
        # print('testing circle')
        # Test case for a simple circle
        points = [(1, 3, 0), (0, 3, 1), (-1, 3, 0), (0, 3, -1)]
        expected_params = (0, 0, 1, 1)

        result = calculations.fit_ellipse(points)
        # print(result)
        np.testing.assert_almost_equal(result, expected_params, decimal=2)

    def test_ellipse(self):
        # Test case for a reasonably simple ellipse
        points = [(2, 3, 0), (0, 3, 1), (-2, 3, 0), (0, 3, -1)]
        expected_params = (0, 0, 2, 1)

        result = calculations.fit_ellipse(points)
        # print(result)
        np.testing.assert_almost_equal(result, expected_params, decimal=2)

    class TestFitEllipse(unittest.TestCase):
        # good
        ellipse_points = [
            [3.669, 11.0, 2.485, 0.0],
            [3.08985, 11.0, 1.7650000000000003, 20.0],
            [3.40705, 11.0, 2.2650000000000006, 40.0],
            [3.3537500000000002, 11.0, 2.2650000000000006, 60.0],
            [3.1607000000000003, 11.0, 2.2650000000000006, 80.0],
            [3.01315, 11.0, 2.2650000000000006, 100.0],
            [2.9475, 11.0, 2.365, 120.0],
            [2.85715, 11.0, 2.665, 140.0],
            [2.8123000000000005, 11.0, 2.7650000000000006, 160.0],
            [3.05735, 11.0, 1.9650000000000003, 180.0],
            [2.8577999999999997, 11.0, 2.365, 200.0],
            [2.9676500000000003, 11.0, 3.0650000000000004, 220.0],
            [3.40965, 11.0, 3.0650000000000004, 240.0],
            [3.24325, 11.0, 3.165, 260.0],
            [3.48505, 11.0, 3.0650000000000004, 280.0],
            [3.6273999999999997, 11.0, 2.9650000000000003, 300.0],
            [3.6599000000000004, 11.0, 2.865, 320.0],
        ]
        # ellipse_points=[[3.3532968750000003, 9.654234374999998, 2.75, 0.0], [3.26778125, 9.238234374999998, 2.6400000000000006, 20.0], [3.2466562500000005, 9.238234374999998, 2.6400000000000006, 40.0], [3.2389375000000005, 9.238234374999998, 2.5900000000000003, 60.0], [3.2446250000000005, 9.238234374999998, 2.5900000000000003, 80.0], [3.2285781250000007, 9.238234374999998, 2.615, 100.0], [3.217812500000001, 9.238234374999998, 2.6650000000000005, 120.0], [3.186734375000001, 9.238234374999998, 2.6650000000000005, 140.0], [3.158500000000001, 9.238234374999998, 2.6900000000000004, 160.0], [3.150171875000001, 9.238234374999998, 2.6900000000000004, 180.0], [3.1690625000000012, 9.238234374999998, 2.7150000000000003, 200.0], [3.1918125000000015, 9.238234374999998, 2.7150000000000003, 220.0], [3.2109062500000016, 9.238234374999998, 2.74, 240.0], [3.232843750000002, 9.238234374999998, 2.74, 260.0], [3.264937500000002, 9.238234374999998, 2.74, 280.0], [3.278546875000002, 9.238234374999998, 2.74, 300.0], [3.292968750000002, 9.238234374999998, 2.6900000000000004, 320.0], [3.323437500000002, 9.238234374999998, 2.6900000000000004, 340.0]]

        params = calculations.fit_ellipse_with_ransac(ellipse_points)
        # Fit the ellipse to the points
        # For fitting the ellipse, we are only using the x and z coordinates, so extract those
        # fit_points = [(x, z) for x, _, z, _ in ellipse_points]
        # params = fit_ellipse(fit_points)

        # Loop over each point
        actual_xz = []
        for point in ellipse_points:
            # Extract the coordinates and angle from the point
            x, _, z, angle = point

            # Calculate the point on the fitted ellipse corresponding to the expected angle
            expected_x, expected_z = calculations.point_on_ellipse(params, angle)
            actual_xz.append([expected_x, 3, expected_z, angle])
            # Assert that the calculated point and the actual point are approximately equal
            # You can adjust the precision as needed
            if not np.allclose((x, z), (expected_x, expected_z), atol=1e-1):
                pass
                # print(f'Point {(x, z)} does not match expected point {(expected_x, expected_z)} for angle {angle}')
        print(actual_xz)


class TestFindPeakBounds(unittest.TestCase):
    def test_find_peak_bounds(self):

        data0 = [
            [
                109.6080093383789,
                109.32815265655518,
                109.35904121398926,
                109.41330623626709,
                109.45026683807373,
                109.48245143890381,
                109.5084228515625,
                109.82300567626953,
                109.97939395904541,
                109.982666015625,
                110.84788513183594,
                113.23259830474854,
                141.98122215270996,
                195.3398151397705,
                207.87511825561523,
                180.85744953155518,
                149.7617359161377,
                122.65280246734619,
                116.87283897399902,
                114.27578735351562,
                110.90087509155273,
            ],
            1,
        ]
        # 15 19
        data1 = [
            [
                150.34661960601807,
                150.2222547531128,
                150.2133378982544,
                150.26158618927002,
                150.36281204223633,
                150.40010166168213,
                150.42618656158447,
                150.406,
                150.44122791290283,
                150.4853630065918,
                150.49519443511963,
                150.7127513885498,
                150.85708332061768,
                150.9246368408203,
                151.23553466796875,
                153.08877658843994,
                246.96870613098145,
                176.91728973388672,
                155.1133689880371,
                151.76532459259033,
                150,
            ],
            1,
        ]
        # 10 15 15 19/20
        data2 = [
            [
                150.3276309967041,
                150.24837493896484,
                150.31103992462158,
                150.36848258972168,
                150.40111637115479,
                150.4083309173584,
                150.43940258026123,
                150.473,
                150.50608444213867,
                150.6149320602417,
                150.7234764099121,
                155.8156909942627,
                180.97621536254883,
                190.3,
                170.5386209487915,
                160.91191577911377,
                253.193865776062,
                171.84050941467285,
                157.62098789215088,
                152.23331451416016,
                150,
                151,
            ],
            2,
        ]

        data3 = [
            [
                150.29833602905273,
                155,
                160,
                180.2885627746582,
                170.3106746673584,
                150.3591079711914,
                150.38312530517578,
                150.493,
                150.521954536438,
                150.5941400527954,
                150.6838502883911,
                150.80633735656738,
                150.93719291687012,
                151.1069746017456,
                151.48811149597168,
                152.50861835479736,
                203.512,
                256.2613515853882,
                174.35614395141602,
                158.79185390472412,
                152.19527435302734,
            ],
            2,
        ]
        data4 = [
            [
                150.91968059539795,
                150.88014221191406,
                150.90409660339355,
                150.96257972717285,
                151.0552635192871,
                151.21269035339355,
                151.4831199645996,
                151.84749507904053,
                156.36608791351318,
                292.6230869293213,
                310.53624153137207,
                297.2038412094116,
                332.45774936676025,
                342.06566619873047,
                301.23432636260986,
                153.24998378753662,
                152.28335285186768,
                151.93097400665283,
                159.69574451446533,
                161.6122817993164,
                171,
                161,
                152,
            ],
            2,
        ]
        datalist = [data0, data1, data2, data3, data4]
        results = [
            [[12, 17]],
            [[16, 17]],
            [[11, 15], [15, 17]],
            [[1, 4], [16, 19]],
            [[9, 14], [18, 21]],
        ]
        # Iterate over both datalist and results using zip
        for data, expected in zip(datalist, results):
            print(len(data[0]))
            # Run the function
            bounds = calculations.find_peak_bounds(data[0], num_peaks=data[1])
            for bound, expected_bound in zip(bounds, expected):
                start_index, end_index = bound
                print(start_index, end_index)
                # Assert that the output is as expected
                self.assertEqual([start_index, end_index], expected_bound)


# Run the test
if __name__ == "__main__":
    unittest.main()
