# Quick Task 001: Add VIP PPS Limit & VIP BPS Limit configuration in Edit Service modal

**Date:** 2026-07-24
**Status:** Done

## Description

Added interactive input fields for VIP PPS Limit and VIP BPS Limit in the tenant `ServiceForm` modal (used when editing or creating a service) so users can configure packet and bandwidth limits per VIP.

## Files Changed

- `control-plane/frontend/src/features/config/services/ServiceForm.tsx` — Add state setters, validation, and NumberInput fields for VIP PPS Limit and VIP BPS Limit.
- `control-plane/frontend/src/features/config/services/ServicesPage.test.tsx` — Add unit test verifying rendering and updating of VIP limits during Service edit.

## Verification

- [x] Vitest tests for ServicesPage and ServiceForm pass without errors.
- [x] Client-side input validation for non-negative numbers works correctly.
