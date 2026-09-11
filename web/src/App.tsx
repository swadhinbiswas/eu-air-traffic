import { lazy } from "react";
import { Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { LandingPage } from "@/pages/LandingPage";

const AnalyticsPage = lazy(() =>
  import("@/pages/AnalyticsPage").then((m) => ({ default: m.AnalyticsPage }))
);
const StoriesPage = lazy(() =>
  import("@/pages/StoriesPage").then((m) => ({ default: m.StoriesPage }))
);
const CatalogPage = lazy(() =>
  import("@/pages/CatalogPage").then((m) => ({ default: m.CatalogPage }))
);
const ExplorerPage = lazy(() =>
  import("@/pages/ExplorerPage").then((m) => ({ default: m.ExplorerPage }))
);
const SqlPage = lazy(() => import("@/pages/SqlPage").then((m) => ({ default: m.SqlPage })));
const OpsPage = lazy(() => import("@/pages/OpsPage").then((m) => ({ default: m.OpsPage })));
const DocsPage = lazy(() => import("@/pages/DocsPage").then((m) => ({ default: m.DocsPage })));

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<LandingPage />} />
        <Route path="analytics" element={<AnalyticsPage />} />
        <Route path="stories" element={<StoriesPage />} />
        <Route path="catalog" element={<CatalogPage />} />
        <Route path="explorer" element={<ExplorerPage />} />
        <Route path="sql" element={<SqlPage />} />
        <Route path="ops" element={<OpsPage />} />
        <Route path="docs" element={<DocsPage />} />
        <Route path="*" element={<LandingPage />} />
      </Route>
    </Routes>
  );
}
