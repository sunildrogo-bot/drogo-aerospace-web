import { useEffect } from 'react';
import { moduleHref } from '../moduleRoutes';

// The Admin panel hasn't been migrated to this frontend yet — this first
// slice covers Login + the Client dashboard. Full-page redirect out to
// the existing server-rendered /admin page, same bridge strategy as
// moduleRoutes.js uses for individual modules.
export default function LegacyRedirect({ to }) {
  useEffect(() => {
    window.location.href = moduleHref(to);
  }, [to]);
  return null;
}
