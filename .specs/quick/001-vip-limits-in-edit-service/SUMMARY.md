# Quick Task 001 Summary: Add VIP PPS & BPS Limit inputs to Edit Service

## Changes Executed

1. **`ServiceForm.tsx`**:
   - Added state setters (`setVipPps`, `setVipBps`) for VIP limits.
   - Added client-side validation for `vip_pps` and `vip_bps` (ensuring non-negative numbers when provided).
   - Rendered two `NumberInput` fields inside a 2-column grid layout for `VIP PPS Limit (Optional)` and `VIP BPS Limit (Optional)`.
   - Plumbed `vip_pps` and `vip_bps` values through to `onSubmit`.

2. **`ServicesPage.test.tsx`**:
   - Updated the unit test to verify that `VIP PPS Limit` and `VIP BPS Limit` input fields display the initial values and submit updated values when saved in the Edit Service modal.

## Test Results

- All 6 tests in `src/features/config/services/ServicesPage.test.tsx` passed.
- All 5 tests in `src/features/config/services-admin/AdminServicesPage.test.tsx` passed.
