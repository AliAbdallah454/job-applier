import "./index.css";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout";
import { Applications } from "./pages/Applications";
import { Cvs } from "./pages/Cvs";
import { GenerationPage } from "./pages/Generation";
import { History } from "./pages/History";
import { JobEmails } from "./pages/JobEmails";
import { Jobs } from "./pages/Jobs";
import { Profile } from "./pages/Profile";

const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queries}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Jobs />} />
            <Route path="generations" element={<History />} />
            <Route path="generations/:id" element={<GenerationPage />} />
            <Route path="applications" element={<Applications />} />
            <Route path="job-emails" element={<JobEmails />} />
            <Route path="cvs" element={<Cvs />} />
            <Route path="profile" element={<Profile />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
