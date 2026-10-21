import { expect, it } from 'vitest'
import { isUnconfirmedPrediction } from './dataSources'

it.each([
  ['PREDICTED_USBOS', 0, true],
  ['PREDICTED_USBOS', 1, false],
  ['PREDICTED_USBOS', null, false],
  ['PREDICTED_USBOS', undefined, false],
  ['SATELLITE_USBOS', 0, false],
  ['BOSTON_OPENDATA', 0, false],
  [null, 0, false],
])('prediction note for source %s and count %s is %s', (source, count, expected) => {
  expect(isUnconfirmedPrediction(source, count)).toBe(expected)
})
