import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  AirVent,
  AlertTriangle,
  Archive,
  BedDouble,
  Building2,
  CheckCheck,
  ChevronDown,
  ChevronUp,
  Copy,
  DoorOpen,
  Flag,
  Heart,
  Home,
  Info,
  LandPlot,
  Layers,
  List,
  LogOut,
  Map as MapIcon,
  MapPin,
  Pencil,
  Phone,
  Plus,
  RefreshCw,
  Ruler,
  Scale,
  Search,
  Loader2,
  Save,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  ShieldAlert,
  Sparkle,
  Sparkles,
  Target,
  TrendingDown,
  UserCheck,
  Users,
  Waves,
  X,
} from "lucide-react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "./styles.css";
import bienIciLogo from "./assets/sources/bien-ici.png";
import greenAcresLogo from "./assets/sources/green-acres.png";
import leboncoinLogo from "./assets/sources/leboncoin.png";
import logicImmoLogo from "./assets/sources/logic-immo.png";
import notairesLogo from "./assets/sources/notaires.png";
import papLogo from "./assets/sources/pap.png";
import paruvenduLogo from "./assets/sources/paruvendu.png";
import selogerLogo from "./assets/sources/seloger.png";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

const FILTERS = [
  ["all", "Toutes"],
  ["new", "Nouvelles"],
  ["favorite", "Shortlist"],
  ["call", "A appeler"],
  ["rejected", "Rejetées"],
];

const SORT_LABELS = {
  score: "Score",
  match: "Pertinence IA",
  price: "Prix croissant",
  surface: "Surface",
  updated: "Plus récentes",
};

const SCAN_PIPELINE_STEPS = [
  ["sources", "Récupération des sources"],
  ["dedup", "Déduplication"],
  ["ai", "Analyse IA"],
];

// Vocabulaire tri-état ("present" / "absent" / "uncertain") produit par le
// worker IA externe (voir docs/openclaw-assistant-worker.md, features_json).
// Ce contrat n'est pas garanti à 100% (worker externe) : toute clé inconnue
// est simplement ignorée, jamais une erreur.
const AI_FEATURE_META = {
  pool: { label: "Piscine", icon: Waves },
  air_conditioning: { label: "Climatisation", icon: AirVent },
  single_storey: { label: "Plain-pied", icon: Layers, boolean: true },
  living_room_to_garden_direct_access: { label: "Accès direct jardin", icon: DoorOpen },
  living_room_to_pool_direct_access: { label: "Accès direct piscine", icon: DoorOpen },
};

function aiFeatureEntries(features) {
  if (!features || typeof features !== "object") return [];
  return Object.entries(AI_FEATURE_META)
    .map(([key, meta]) => {
      const raw = features[key];
      if (meta.boolean) {
        if (raw !== true) return null;
        return { key, meta, state: "present" };
      }
      if (raw !== "present" && raw !== "uncertain") return null;
      return { key, meta, state: raw };
    })
    .filter(Boolean);
}

function AIFeatureBadges({ features, detailed = false }) {
  const entries = aiFeatureEntries(features);
  if (entries.length === 0) return null;

  return (
    <div className={`ai-feature-row${detailed ? " is-detailed" : ""}`}>
      {entries.map(({ key, meta, state }) => {
        const Icon = meta.icon;
        const title = state === "uncertain" ? `${meta.label} (probable, non confirmé)` : meta.label;
        return (
          <span key={key} className={`ai-feature-badge${state === "uncertain" ? " is-uncertain" : ""}`} title={title}>
            <Icon size={detailed ? 14 : 12} />
            {detailed && meta.label}
          </span>
        );
      })}
    </div>
  );
}

function formatPrice(value) {
  if (!value) return "Prix NC";
  return new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(value);
}

function pricePerM2(listing) {
  if (!listing.price_eur || !listing.living_area_m2) return "?";
  return `${formatPrice(Math.round(listing.price_eur / listing.living_area_m2))} / m²`;
}

function formatPricePerM2Value(value) {
  if (!Number.isFinite(value) || value <= 0) return null;
  return `${new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 0 }).format(Math.round(value))} €/m²`;
}

function formatPricePerM2(listing) {
  if (!listing || !listing.price_eur || !listing.living_area_m2) return null;
  return formatPricePerM2Value(listing.price_eur / listing.living_area_m2);
}

function formatSignedAmount(value) {
  if (value === null || value === undefined || !Number.isFinite(value)) return null;
  const formatted = new Intl.NumberFormat("fr-FR", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 0,
    signDisplay: "always",
  }).format(value);
  return formatted;
}

function formatShortDate(isoString) {
  try {
    return new Intl.DateTimeFormat("fr-FR", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(isoString));
  } catch {
    return isoString;
  }
}

function daysOnMarketLabel(listing) {
  if (listing?.days_on_market === null || listing?.days_on_market === undefined) return null;
  const days = listing.days_on_market;
  const unit = days === 1 ? "jour" : "jours";
  return listing.off_market ? `Retirée après ${days} ${unit}` : `Sur le marché depuis ${days} ${unit}`;
}

function relativeTimeFr(isoString) {
  if (!isoString) return null;
  const then = new Date(isoString).getTime();
  if (!Number.isFinite(then)) return null;
  const minutes = Math.round((Date.now() - then) / 60000);
  if (minutes < 1) return "à l'instant";
  if (minutes < 60) return `il y a ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `il y a ${hours} h`;
  const days = Math.round(hours / 24);
  return `il y a ${days} j`;
}

function formatClockFr(isoString) {
  if (!isoString) return null;
  const date = new Date(isoString);
  if (!Number.isFinite(date.getTime())) return null;
  const time = new Intl.DateTimeFormat("fr-FR", { hour: "2-digit", minute: "2-digit" }).format(date).replace(":", "h");
  const sameDay = date.toDateString() === new Date().toDateString();
  if (sameDay) return time;
  return `${new Intl.DateTimeFormat("fr-FR", { weekday: "short" }).format(date)} ${time}`;
}

// Identité visuelle des portails agrégés (favicons bundlés en local : pas de
// hotlink qui casse ou qui fuite la navigation vers un tiers).
const SOURCE_META = {
  "green-acres": { label: "Green-Acres", logo: greenAcresLogo },
  "bien-ici": { label: "Bien'ici", logo: bienIciLogo },
  seloger: { label: "SeLoger", logo: selogerLogo },
  leboncoin: { label: "LeBonCoin", logo: leboncoinLogo },
  paruvendu: { label: "ParuVendu", logo: paruvenduLogo },
  pap: { label: "PAP", logo: papLogo },
  "logic-immo": { label: "Logic-Immo", logo: logicImmoLogo },
  notaires: { label: "Notaires", logo: notairesLogo },
};

function sourceMeta(source) {
  return SOURCE_META[source] || { label: source, logo: null };
}

function isSourceUrlUsable(src) {
  if (!src?.url) return false;
  try {
    const url = new URL(src.url);
    const path = url.pathname.replace(/\/+$/, "");
    if (src.source === "logic-immo" && url.hostname.endsWith("logic-immo.com") && path === "") {
      return false;
    }
    return true;
  } catch {
    return false;
  }
}

function SourceLogo({ source, size = 14 }) {
  const meta = sourceMeta(source);
  if (!meta.logo) return <Building2 size={size} aria-hidden="true" />;
  return <img className="source-logo" src={meta.logo} alt="" width={size} height={size} loading="lazy" />;
}

function SourceBadge({ src, onClick }) {
  const meta = sourceMeta(src.source);
  const content = (
    <>
      <SourceLogo source={src.source} size={13} />
      {meta.label}
    </>
  );
  if (!isSourceUrlUsable(src)) {
    return (
      <span className="source-badge" title={`Lien ${meta.label} indisponible`}>
        {content}
      </span>
    );
  }
  return (
    <a
      className="source-badge source-badge-link"
      href={src.url}
      target="_blank"
      rel="noreferrer"
      title={`Voir l'annonce sur ${meta.label}`}
      onClick={onClick}
    >
      {content}
    </a>
  );
}

function ScanPipeline({ activeStage }) {
  if (!activeStage) return null;
  const activeIndex = SCAN_PIPELINE_STEPS.findIndex(([key]) => key === activeStage);

  return (
    <div className="scan-pipeline" aria-live="polite">
      {SCAN_PIPELINE_STEPS.map(([key, label], index) => {
        const isActive = key === activeStage;
        const isDone = activeIndex > index;
        return (
          <span className={`scan-step${isActive ? " is-active" : ""}${isDone ? " is-done" : ""}`} key={key}>
            {isDone ? <CheckCheck size={13} /> : key === "ai" ? <Sparkles size={13} /> : <Search size={13} />}
            {label}
          </span>
        );
      })}
    </div>
  );
}

function formatSignedPercent(ratio) {
  if (ratio === null || ratio === undefined || !Number.isFinite(ratio)) return null;
  const percent = Math.round(ratio * 1000) / 10;
  const formatted = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 1, signDisplay: "always" }).format(percent);
  return `${formatted}%`;
}

function dvfDeltaTone(ratio) {
  if (ratio === null || ratio === undefined || !Number.isFinite(ratio)) return "neutral";
  if (ratio > 0.3) return "bad";
  if (ratio <= 0) return "good";
  return "neutral";
}

const RISK_LABELS = {
  inondation: "Inondation",
  argiles: "Argiles",
  feu_foret: "Feu de forêt",
  seisme: "Séisme",
  radon: "Radon",
  risque_cotier: "Recul du trait de côte",
  mouvement_terrain: "Mouvement de terrain",
  pollution_sols: "Pollution des sols",
};

function presentRisks(risks) {
  if (!risks || typeof risks !== "object") return [];
  return Object.entries(RISK_LABELS)
    .filter(([code]) => Boolean(risks[code]))
    .map(([code, label]) => ({ code, label }));
}

function scoreTier(score) {
  if (score === null || score === undefined) return "score-mid";
  if (score >= 70) return "score-good";
  if (score >= 40) return "score-mid";
  return "score-low";
}

const SCORE_TIER_COLORS = {
  "score-good": "#1f7a4d",
  "score-mid": "#b3781a",
  "score-low": "#b0402f",
};

function scoreTierColor(score) {
  return SCORE_TIER_COLORS[scoreTier(score)] || SCORE_TIER_COLORS["score-mid"];
}

function hasCoordinates(listing) {
  return (
    typeof listing?.latitude === "number" &&
    Number.isFinite(listing.latitude) &&
    typeof listing?.longitude === "number" &&
    Number.isFinite(listing.longitude)
  );
}

function renderListItem(item) {
  if (typeof item === "string") return item;
  if (item && typeof item === "object") {
    return item.label || item.text || item.reason || JSON.stringify(item);
  }
  return String(item);
}

function PriceSparkline({ points }) {
  const values = points.map((point) => point.price_eur).filter((value) => typeof value === "number" && Number.isFinite(value));
  if (values.length < 2) return null;
  const width = 280;
  const height = 56;
  const padding = 6;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const step = (width - padding * 2) / (values.length - 1);
  const coords = values.map((value, index) => {
    const x = padding + index * step;
    const y = padding + (height - padding * 2) * (1 - (value - min) / range);
    return [x, y];
  });
  const path = coords.map(([x, y], index) => `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const isDrop = values[values.length - 1] < values[0];
  return (
    <svg className="price-sparkline" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden="true">
      <path d={path} fill="none" stroke={isDrop ? "var(--color-good)" : "var(--color-ink-faint)"} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      {coords.map(([x, y], index) => (
        <circle key={index} cx={x} cy={y} r="2.5" fill={isDrop ? "var(--color-good)" : "var(--color-ink-faint)"} />
      ))}
    </svg>
  );
}

const EMPTY_STATE_COPY = {
  all: {
    title: "Aucune annonce pour le moment",
    body: "Ajoute une ville à suivre puis lance un scan pour commencer à recevoir des annonces.",
  },
  new: {
    title: "Pas de nouvelle annonce",
    body: "Lance un scan pour vérifier si de nouveaux biens sont apparus sur les portails suivis.",
  },
  favorite: {
    title: "Aucune annonce en shortlist",
    body: "Marque tes coups de cœur depuis une fiche annonce pour les retrouver ici.",
  },
  call: {
    title: "Rien à appeler pour l'instant",
    body: "Les annonces marquées « A appeler » apparaîtront dans cet onglet.",
  },
  rejected: {
    title: "Aucune annonce rejetée",
    body: "Les biens que tu écartes seront listés ici pour garder l'historique.",
  },
};

const DEFAULT_STANDARD_FILTERS = {
  max_price_eur: "",
  min_living_area_m2: "",
  min_land_area_m2: "",
  min_bedrooms: "",
  sort: "score",
  price_dropped_only: false,
  include_off_market: false,
  cities: [],
};

function App() {
  const [token, setToken] = useState(() => localStorage.getItem("maisonScoutToken") || "");
  const [user, setUser] = useState(null);
  const [listings, setListings] = useState([]);
  const [runs, setRuns] = useState([]);
  const [sourcesStatus, setSourcesStatus] = useState([]);
  const [profiles, setProfiles] = useState([]);
  const [naturalProfiles, setNaturalProfiles] = useState([]);
  const [naturalPromptDraft, setNaturalPromptDraft] = useState("");
  const [naturalNameDraft, setNaturalNameDraft] = useState("");
  const [selectedNaturalProfile, setSelectedNaturalProfile] = useState(null);
  const [status, setStatus] = useState("all");
  const [loading, setLoading] = useState(false);
  const [scanStage, setScanStage] = useState(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [markingSeen, setMarkingSeen] = useState(false);
  const [authMode, setAuthMode] = useState("login");
  const [authForm, setAuthForm] = useState({ email: "", password: "", display_name: "", invite_code: "" });
  const [newCity, setNewCity] = useState("");
  const [standardFilters, setStandardFilters] = useState(DEFAULT_STANDARD_FILTERS);
  const [selectedListing, setSelectedListing] = useState(null);
  const [selectedProfile, setSelectedProfile] = useState(null);
  const [noteDraft, setNoteDraft] = useState("");
  const [error, setError] = useState("");
  const [comparison, setComparison] = useState([]);
  const [comparisonError, setComparisonError] = useState("");
  const [showComparison, setShowComparison] = useState(false);
  const [priceHistory, setPriceHistory] = useState([]);
  const [priceHistoryLoading, setPriceHistoryLoading] = useState(false);
  const [viewMode, setViewMode] = useState("list");
  // La zone "Ma recherche" (villes + recherches IA) est de la configuration
  // ponctuelle : TOUJOURS repliée à l'arrivée (pas de persistance — un panneau
  // ouvert une fois ne doit pas revenir ouvert à chaque visite).
  const [searchConfigOpen, setSearchConfigOpen] = useState(false);
  // Sur mobile, les filtres vivent dans une bottom-sheet ouverte à la demande
  // (sur desktop le panneau reste affiché en permanence, la classe est ignorée).
  const [showFiltersSheet, setShowFiltersSheet] = useState(false);
  const [showAdmin, setShowAdmin] = useState(false);
  const [adminUsers, setAdminUsers] = useState([]);
  const [adminInviteCodes, setAdminInviteCodes] = useState([]);
  const [adminLoading, setAdminLoading] = useState(false);
  const [adminError, setAdminError] = useState("");
  const [adminNoteDraft, setAdminNoteDraft] = useState("");
  const [adminGenerating, setAdminGenerating] = useState(false);

  const mapContainerRef = useRef(null);
  const mapInstanceRef = useRef(null);
  const mapMarkersRef = useRef([]);
  const sortTouchedRef = useRef(false);

  function authHeaders() {
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  async function loadListings() {
    if (!token) return;
    try {
      const listingsUrl = new URL(`${API_URL}/api/listings`);
      if (standardFilters.include_off_market) {
        listingsUrl.searchParams.set("include_off_market", "true");
      }
      const [
        meResponse,
        listingsResponse,
        runsResponse,
        profilesResponse,
        comparisonResponse,
        naturalProfilesResponse,
        sourcesStatusResponse,
      ] = await Promise.all([
        fetch(`${API_URL}/api/me`, { headers: authHeaders() }),
        fetch(listingsUrl, { headers: authHeaders() }),
        fetch(`${API_URL}/api/crawl-runs`, { headers: authHeaders() }),
        fetch(`${API_URL}/api/search-profiles`, { headers: authHeaders() }),
        fetch(`${API_URL}/api/comparison`, { headers: authHeaders() }),
        fetch(`${API_URL}/api/natural-search-profiles`, { headers: authHeaders() }),
        fetch(`${API_URL}/api/sources/status`, { headers: authHeaders() }),
      ]);
      if (meResponse.status === 401) {
        logout();
        return;
      }
      setUser(await meResponse.json());
      setListings(await listingsResponse.json());
      setRuns(await runsResponse.json());
      setProfiles(await profilesResponse.json());
      setComparison(await comparisonResponse.json());
      setNaturalProfiles(naturalProfilesResponse.ok ? await naturalProfilesResponse.json() : []);
      // Tolérant : l'endpoint peut être absent le temps que le backend se déploie.
      setSourcesStatus(sourcesStatusResponse.ok ? await sourcesStatusResponse.json() : []);
    } finally {
      setInitialLoading(false);
    }
  }

  async function markAllSeen() {
    if (!token || markingSeen) return;
    setMarkingSeen(true);
    try {
      await fetch(`${API_URL}/api/listings/mark-seen`, { method: "POST", headers: authHeaders() });
      await loadListings();
    } finally {
      setMarkingSeen(false);
    }
  }

  async function openAdmin() {
    setShowAdmin(true);
    setAdminError("");
    setAdminLoading(true);
    try {
      const [usersResponse, codesResponse] = await Promise.all([
        fetch(`${API_URL}/api/admin/users`, { headers: authHeaders() }),
        fetch(`${API_URL}/api/admin/invite-codes`, { headers: authHeaders() }),
      ]);
      if (usersResponse.status === 403 || codesResponse.status === 403) {
        setAdminError("Accès administrateur requis.");
        setAdminUsers([]);
        setAdminInviteCodes([]);
        return;
      }
      setAdminUsers(usersResponse.ok ? await usersResponse.json() : []);
      setAdminInviteCodes(codesResponse.ok ? await codesResponse.json() : []);
    } catch {
      setAdminError("Impossible de charger les données d'administration.");
    } finally {
      setAdminLoading(false);
    }
  }

  async function generateInviteCode(event) {
    event.preventDefault();
    if (adminGenerating) return;
    setAdminGenerating(true);
    setAdminError("");
    try {
      const response = await fetch(`${API_URL}/api/admin/invite-codes`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ note: adminNoteDraft.trim() || undefined }),
      });
      if (!response.ok) {
        setAdminError("Impossible de générer un code.");
        return;
      }
      const created = await response.json();
      setAdminInviteCodes((current) => [created, ...current]);
      setAdminNoteDraft("");
    } finally {
      setAdminGenerating(false);
    }
  }

  async function toggleInviteCodeActive(code) {
    setAdminError("");
    const response = await fetch(`${API_URL}/api/admin/invite-codes/${code.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ active: !code.active }),
    });
    if (!response.ok) {
      setAdminError("Impossible de mettre à jour le code.");
      return;
    }
    const updated = await response.json();
    setAdminInviteCodes((current) => current.map((item) => (item.id === updated.id ? updated : item)));
  }

  async function submitAuth(event) {
    event.preventDefault();
    setError("");
    const response = await fetch(`${API_URL}/api/auth/${authMode}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(authForm),
    });
    if (!response.ok) {
      setError(authMode === "login" ? "Connexion impossible" : "Inscription impossible");
      return;
    }
    const data = await response.json();
    localStorage.setItem("maisonScoutToken", data.token);
    setToken(data.token);
    setUser(data.user);
  }

  function logout() {
    localStorage.removeItem("maisonScoutToken");
    setToken("");
    setUser(null);
    setListings([]);
    setRuns([]);
    setProfiles([]);
    setComparison([]);
    setShowComparison(false);
    setNaturalProfiles([]);
    setSelectedNaturalProfile(null);
    setInitialLoading(true);
    setShowAdmin(false);
    setAdminUsers([]);
    setAdminInviteCodes([]);
    setViewMode("list");
  }

  async function refreshSourcesStatus() {
    const response = await fetch(`${API_URL}/api/sources/status`, { headers: authHeaders() });
    if (!response.ok) return null;
    const statuses = await response.json();
    setSourcesStatus(statuses);
    return statuses;
  }

  async function runAllCrawlers() {
    setLoading(true);
    setScanStage("sources");
    try {
      const response = await fetch(`${API_URL}/api/crawl/request`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({}),
      });
      if (response.status === 404) {
        // Backend pas encore à jour : ancien scan synchrone.
        await fetch(`${API_URL}/api/crawl/all`, { method: "POST", headers: authHeaders() });
        setScanStage("dedup");
        await loadListings();
        return;
      }
      // Les jobs tournent en tâche de fond (backend) ou attendent leur
      // exécuteur (OpenClaw) : on suit l'avancement via le statut des sources,
      // puis on recharge. Au-delà de 2 min on rend la main — le bandeau
      // continue d'afficher les jobs en attente.
      const deadline = Date.now() + 2 * 60 * 1000;
      let active = true;
      while (active && Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 4000));
        const statuses = await refreshSourcesStatus();
        if (!statuses) break;
        active = statuses.some((st) => st.job_status);
      }
      setScanStage("dedup");
      await new Promise((resolve) => setTimeout(resolve, 1200));
      setScanStage("ai");
      await new Promise((resolve) => setTimeout(resolve, 1200));
      await loadListings();
    } finally {
      setLoading(false);
      setScanStage(null);
    }
  }

  async function setListingStatus(id, nextStatus, note = undefined) {
    const body = {};
    if (nextStatus !== undefined) body.status = nextStatus;
    if (note !== undefined) body.note = note;
    await fetch(`${API_URL}/api/listings/${id}/status`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(body),
    });
    await loadListings();
  }

  async function saveNote(id, note) {
    await setListingStatus(id, undefined, note);
  }

  const comparisonIds = useMemo(() => new Set(comparison.map((listing) => listing.id)), [comparison]);
  const newListingsCount = useMemo(() => listings.filter((listing) => listing.is_new === true).length, [listings]);
  const activeNaturalCount = useMemo(
    () => naturalProfiles.filter((profile) => profile.is_active).length,
    [naturalProfiles]
  );
  const defaultSort = activeNaturalCount > 0 ? "match" : "score";

  useEffect(() => {
    if (activeNaturalCount === 0 || sortTouchedRef.current) return;
    setStandardFilters((current) => (current.sort === "match" ? current : { ...current, sort: "match" }));
  }, [activeNaturalCount]);

  const activeFilterCount = useMemo(
    () =>
      [
        standardFilters.max_price_eur,
        standardFilters.min_living_area_m2,
        standardFilters.min_land_area_m2,
        standardFilters.min_bedrooms,
      ].filter((value) => `${value}`.trim() !== "").length +
      (standardFilters.price_dropped_only ? 1 : 0) +
      (standardFilters.include_off_market ? 1 : 0) +
      standardFilters.cities.length,
    [standardFilters]
  );
  // Villes réellement présentes dans les annonces (dérivé) : n'affiche le
  // filtre ville que si on en suit plus d'une, sinon ça n'a aucune utilité.
  const availableCities = useMemo(() => {
    const seen = new Set();
    for (const listing of listings) {
      if (listing.city) seen.add(listing.city);
    }
    return [...seen].sort((a, b) => a.localeCompare(b));
  }, [listings]);
  // Sans ville configurée il n'y a rien à afficher : on force l'ouverture pour
  // que l'onboarding reste évident.
  const searchConfigExpanded = searchConfigOpen || (!initialLoading && profiles.length === 0);
  // Portails réellement présents dans les annonces affichées (dérivé, donc
  // toujours honnête — la démo est exclue du bandeau).
  const aggregatedSources = useMemo(() => {
    const seen = new Set();
    for (const listing of listings) {
      for (const src of listing.sources || []) {
        if (src.source && src.source !== "demo") seen.add(src.source);
      }
    }
    return [...seen].sort();
  }, [listings]);

  async function addToComparison(id) {
    setComparisonError("");
    const response = await fetch(`${API_URL}/api/comparison/${id}`, { method: "POST", headers: authHeaders() });
    if (!response.ok) {
      const detail = await response.json().catch(() => null);
      setComparisonError(detail?.detail || "Impossible d'ajouter au comparatif");
      return;
    }
    setComparison(await response.json());
  }

  async function removeFromComparison(id) {
    const response = await fetch(`${API_URL}/api/comparison/${id}`, { method: "DELETE", headers: authHeaders() });
    if (response.ok) {
      setComparison(await response.json());
    }
  }

  function toggleComparison(id) {
    if (comparisonIds.has(id)) {
      removeFromComparison(id);
    } else {
      addToComparison(id);
    }
  }

  async function addCity(event) {
    event.preventDefault();
    if (!newCity.trim()) return;
    const response = await fetch(`${API_URL}/api/search-profiles`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({
        city: newCity.trim(),
        max_price_eur: null,
        min_living_area_m2: null,
        min_land_area_m2: null,
        min_bedrooms: null,
      }),
    });
    setNewCity("");
    await loadListings();
    if (response.ok) {
      const created = await response.json();
      // Ouvre directement les réglages pour laisser définir budget/surface/terrain/chambres.
      setSelectedProfile(created);
    }
  }

  async function deleteProfile(profileId) {
    await fetch(`${API_URL}/api/search-profiles/${profileId}`, {
      method: "DELETE",
      headers: authHeaders(),
    });
    await loadListings();
  }

  async function saveProfile(profile) {
    await fetch(`${API_URL}/api/search-profiles/${profile.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({
        name: profile.name,
        city: profile.city,
        source: profile.source,
        max_price_eur: numberOrNull(profile.max_price_eur),
        min_living_area_m2: numberOrNull(profile.min_living_area_m2),
        min_land_area_m2: numberOrNull(profile.min_land_area_m2),
        min_bedrooms: numberOrNull(profile.min_bedrooms),
      }),
    });
    setSelectedProfile(null);
    await loadListings();
  }

  async function createNaturalProfile(event) {
    event.preventDefault();
    if (!naturalPromptDraft.trim()) return;
    await fetch(`${API_URL}/api/natural-search-profiles`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({
        name: naturalNameDraft.trim() || undefined,
        raw_prompt: naturalPromptDraft.trim(),
      }),
    });
    setNaturalPromptDraft("");
    setNaturalNameDraft("");
    await loadListings();
  }

  async function saveNaturalProfile(profile) {
    await fetch(`${API_URL}/api/natural-search-profiles/${profile.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({
        name: profile.name,
        raw_prompt: profile.raw_prompt,
        is_active: profile.is_active,
      }),
    });
    setSelectedNaturalProfile(null);
    await loadListings();
  }

  async function toggleNaturalProfileActive(profile) {
    await fetch(`${API_URL}/api/natural-search-profiles/${profile.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ is_active: !profile.is_active }),
    });
    await loadListings();
  }

  async function deleteNaturalProfile(profileId) {
    await fetch(`${API_URL}/api/natural-search-profiles/${profileId}`, {
      method: "DELETE",
      headers: authHeaders(),
    });
    await loadListings();
  }

  function openListing(listing) {
    setSelectedListing(listing);
    setNoteDraft(listing.note || "");
  }

  function toggleSearchConfig() {
    setSearchConfigOpen((open) => !open);
  }

  useEffect(() => {
    loadListings();
  }, [token]);

  useEffect(() => {
    if (!token) return;
    loadListings();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [standardFilters.include_off_market]);

  useEffect(() => {
    // Échap ferme la modale visuellement au premier plan (ordre inverse du
    // z-order de rendu : admin/comparatif par-dessus la fiche annonce).
    function onKeyDown(event) {
      if (event.key !== "Escape") return;
      if (showFiltersSheet) setShowFiltersSheet(false);
      else if (showAdmin) setShowAdmin(false);
      else if (showComparison) setShowComparison(false);
      else if (selectedNaturalProfile) setSelectedNaturalProfile(null);
      else if (selectedProfile) setSelectedProfile(null);
      else if (selectedListing) setSelectedListing(null);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [showFiltersSheet, showAdmin, showComparison, selectedNaturalProfile, selectedProfile, selectedListing]);

  useEffect(() => {
    if (!selectedListing || !token) {
      setPriceHistory([]);
      return;
    }
    let cancelled = false;
    setPriceHistoryLoading(true);
    setPriceHistory([]);
    fetch(`${API_URL}/api/listings/${selectedListing.id}/price-history`, { headers: authHeaders() })
      .then((response) => (response.ok ? response.json() : []))
      .then((data) => {
        if (!cancelled) setPriceHistory(Array.isArray(data) ? data : []);
      })
      .catch(() => {
        if (!cancelled) setPriceHistory([]);
      })
      .finally(() => {
        if (!cancelled) setPriceHistoryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedListing?.id, token]);

  const filtered = useMemo(() => {
    const matchesNumber = (value, filterValue, mode) => {
      if (!filterValue) return true;
      // Une donnée manquante ne doit pas masquer l'annonce : on préfère
      // laisser passer plutôt que cacher à tort un bien mal extrait.
      if (value === null || value === undefined) return true;
      const parsed = Number(filterValue);
      if (!Number.isFinite(parsed)) return true;
      return mode === "max" ? value <= parsed : value >= parsed;
    };
    const visible = listings
      .filter((listing) => status === "all" || listing.status === status)
      .filter((listing) => standardFilters.cities.length === 0 || standardFilters.cities.includes(listing.city))
      .filter((listing) => matchesNumber(listing.price_eur, standardFilters.max_price_eur, "max"))
      .filter((listing) => matchesNumber(listing.living_area_m2, standardFilters.min_living_area_m2, "min"))
      .filter((listing) => matchesNumber(listing.land_area_m2, standardFilters.min_land_area_m2, "min"))
      .filter((listing) => matchesNumber(listing.bedrooms, standardFilters.min_bedrooms, "min"))
      .filter((listing) => !standardFilters.price_dropped_only || listing.price_dropped === true);
    return [...visible].sort((a, b) => {
      if (standardFilters.sort === "price") return (a.price_eur ?? Number.MAX_SAFE_INTEGER) - (b.price_eur ?? Number.MAX_SAFE_INTEGER);
      if (standardFilters.sort === "surface") return (b.living_area_m2 ?? 0) - (a.living_area_m2 ?? 0);
      if (standardFilters.sort === "updated") return 0;
      if (standardFilters.sort === "match") {
        if (a.match_score === null || a.match_score === undefined) return 1;
        if (b.match_score === null || b.match_score === undefined) return -1;
        return b.match_score - a.match_score;
      }
      return (b.score ?? 0) - (a.score ?? 0);
    });
  }, [listings, status, standardFilters]);

  const activeListing = selectedListing ? listings.find((listing) => listing.id === selectedListing.id) || selectedListing : null;

  const geoListings = useMemo(() => filtered.filter(hasCoordinates), [filtered]);

  useEffect(() => {
    if (viewMode !== "map" || !mapContainerRef.current) return;

    if (!mapInstanceRef.current) {
      const map = L.map(mapContainerRef.current, { attributionControl: true }).setView([46.6, 2.4], 6);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: "&copy; OpenStreetMap contributors",
      }).addTo(map);
      mapInstanceRef.current = map;
    }
    const map = mapInstanceRef.current;

    // Nettoie les marqueurs précédents avant de redessiner.
    mapMarkersRef.current.forEach((marker) => marker.remove());
    mapMarkersRef.current = [];

    geoListings.forEach((listing) => {
      const marker = L.circleMarker([listing.latitude, listing.longitude], {
        radius: 9,
        color: scoreTierColor(listing.score),
        weight: 2,
        fillColor: scoreTierColor(listing.score),
        fillOpacity: 0.75,
      }).addTo(map);
      const popupNode = document.createElement("div");
      popupNode.className = "map-popup";
      const title = document.createElement("p");
      title.className = "map-popup-title";
      title.textContent = listing.title || "Annonce";
      const price = document.createElement("p");
      price.className = "map-popup-price";
      price.textContent = formatPrice(listing.price_eur);
      const city = document.createElement("p");
      city.className = "map-popup-city";
      city.textContent = `${listing.city || ""} ${listing.postal_code || ""}`.trim();
      popupNode.append(title, price, city);
      const popupSource = listing.sources?.find(isSourceUrlUsable);
      if (popupSource) {
        const link = document.createElement("a");
        link.href = popupSource.url;
        link.target = "_blank";
        link.rel = "noreferrer";
        link.className = "map-popup-link";
        link.textContent = "Voir l'annonce";
        popupNode.appendChild(link);
      }
      const detailButton = document.createElement("button");
      detailButton.type = "button";
      detailButton.className = "map-popup-detail";
      detailButton.textContent = "Voir la fiche";
      detailButton.addEventListener("click", () => openListing(listing));
      popupNode.appendChild(detailButton);
      marker.bindPopup(popupNode);
      mapMarkersRef.current.push(marker);
    });

    if (geoListings.length > 0) {
      const bounds = L.latLngBounds(geoListings.map((listing) => [listing.latitude, listing.longitude]));
      map.fitBounds(bounds, { padding: [32, 32], maxZoom: 15 });
    }

    // Leaflet a besoin d'un recalcul de taille quand le conteneur vient d'apparaître.
    setTimeout(() => map.invalidateSize(), 0);
  }, [viewMode, geoListings]);

  useEffect(() => {
    return () => {
      if (mapInstanceRef.current) {
        mapInstanceRef.current.remove();
        mapInstanceRef.current = null;
      }
    };
  }, []);

  if (!token) {
    return (
      <main className="auth-layout">
        <section className="auth-panel">
          <span className="auth-brand">
            <Home size={16} />
            Maison Scout
          </span>
          <h1>{authMode === "login" ? "Content de te revoir" : "Rejoindre le groupe"}</h1>
          <p>Connecte-toi pour suivre tes villes, trier les annonces et lancer les scans.</p>
          <div className="auth-tabs">
            <button type="button" className={authMode === "login" ? "active" : ""} onClick={() => setAuthMode("login")}>
              Connexion
            </button>
            <button type="button" className={authMode === "register" ? "active" : ""} onClick={() => setAuthMode("register")}>
              Inscription
            </button>
          </div>
          <form onSubmit={submitAuth}>
            {authMode === "register" && (
              <>
                <input
                  placeholder="Nom affiché"
                  value={authForm.display_name}
                  onChange={(event) => setAuthForm({ ...authForm, display_name: event.target.value })}
                />
                <input
                  placeholder="Code invitation"
                  value={authForm.invite_code}
                  onChange={(event) => setAuthForm({ ...authForm, invite_code: event.target.value })}
                />
              </>
            )}
            <input
              placeholder="Email"
              type="email"
              value={authForm.email}
              onChange={(event) => setAuthForm({ ...authForm, email: event.target.value })}
            />
            <input
              placeholder="Mot de passe"
              type="password"
              value={authForm.password}
              onChange={(event) => setAuthForm({ ...authForm, password: event.target.value })}
            />
            {error && <p className="error">{error}</p>}
            <button className="primary" type="submit">
              {authMode === "login" ? "Se connecter" : "Créer le compte"}
            </button>
          </form>
          <button className="link-button" onClick={() => setAuthMode(authMode === "login" ? "register" : "login")}>
            {authMode === "login" ? "Pas encore de compte ? Créer un compte" : "J'ai déjà un compte"}
          </button>
        </section>
      </main>
    );
  }

  return (
    <main>
      <header className="topbar">
        <div className="topbar-brand">
          <span className="topbar-brand-mark">
            <Home size={22} />
          </span>
          <div>
            <h1>Maison Scout</h1>
            <p>{user?.display_name || "Compte"} · annonces centralisées par ville suivie.</p>
          </div>
        </div>
        <div className="topbar-actions">
          <div className="scan-action">
            <button className="primary scan-trigger" onClick={runAllCrawlers} disabled={loading} title="Scanner les sources">
              {loading ? <Loader2 size={18} className="spin" /> : <RefreshCw size={18} />}
              <span className="scan-label">
                {loading
                  ? scanStage === "dedup"
                    ? "Déduplication..."
                    : scanStage === "ai"
                    ? "Analyse IA..."
                    : "Récupération..."
                  : "Scanner"}
              </span>
            </button>
            <ScanPipeline activeStage={scanStage} />
          </div>
          <button
            className="ghost compare-trigger"
            onClick={() => setShowComparison(true)}
            disabled={comparison.length === 0}
            title="Comparer les annonces sélectionnées"
          >
            <Scale size={18} />
            <span className="btn-label">Comparer</span>
            {comparison.length > 0 && <span className="compare-count">{comparison.length}</span>}
          </button>
          <button
            className="ghost mark-seen-trigger"
            onClick={markAllSeen}
            disabled={markingSeen || newListingsCount === 0}
            title="Marquer toutes les annonces comme vues"
          >
            {markingSeen ? <Loader2 size={18} className="spin" /> : <CheckCheck size={18} />}
            <span className="btn-label">Tout marquer comme vu</span>
            {newListingsCount > 0 && (
              <span className="new-count">
                {newListingsCount}
                <span className="btn-label"> nouvelle{newListingsCount > 1 ? "s" : ""}</span>
              </span>
            )}
          </button>
          {user?.is_admin && (
            <button className="icon-button" title="Administration" onClick={openAdmin}>
              <ShieldCheck size={18} />
            </button>
          )}
          <button className="icon-button" title="Déconnexion" onClick={logout}>
            <LogOut size={18} />
          </button>
        </div>
      </header>
      {comparisonError && <p className="error compare-error">{comparisonError}</p>}

      <section className="search-config">
        <button
          type="button"
          className="search-config-summary"
          onClick={toggleSearchConfig}
          aria-expanded={searchConfigExpanded}
        >
          <span className="search-config-title">
            <Search size={15} />
            Ma recherche
          </span>
          <span className="search-config-chips">
            {profiles.map((profile) => (
              <span className="search-config-city" key={profile.id}>
                <MapPin size={12} />
                {profile.city}
              </span>
            ))}
            {profiles.length === 0 && <span className="search-config-hint">Aucune ville suivie</span>}
            <span className={`search-config-natural${activeNaturalCount > 0 ? " has-active" : ""}`}>
              <Sparkles size={12} />
              {activeNaturalCount > 0
                ? `${activeNaturalCount} recherche${activeNaturalCount > 1 ? "s" : ""} IA active${activeNaturalCount > 1 ? "s" : ""}`
                : "Pas de recherche IA"}
            </span>
          </span>
          <span className="search-config-toggle">
            {searchConfigExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
            {searchConfigExpanded ? "Replier" : "Modifier"}
          </span>
        </button>

        {searchConfigExpanded && (
          <>
      <section className="profiles">
        <div className="profile-list">
          {profiles.map((profile) => (
            <span className="profile-chip" key={profile.id}>
              <MapPin size={14} className="profile-chip-icon" />
              {profile.city}
              <button title="Réglages" onClick={() => setSelectedProfile(profile)}>
                <Settings size={14} />
              </button>
              <button title="Supprimer" onClick={() => deleteProfile(profile.id)}>
                <X size={14} />
              </button>
            </span>
          ))}
        </div>
        <form className="add-city-form" onSubmit={addCity}>
          <input placeholder="Ajouter une ville" value={newCity} onChange={(event) => setNewCity(event.target.value)} />
          <button title="Ajouter" type="submit">
            <Plus size={18} />
          </button>
        </form>
        <p className="add-city-hint">
          Budget, surface, terrain et chambres se règlent ensuite par ville via l'icône <Settings size={12} />.
        </p>
      </section>

      <section className="natural-profiles">
        <div className="natural-profiles-header">
          <Sparkles size={17} />
          <span>Recherches en langage naturel</span>
        </div>
        {naturalProfiles.length > 0 && (
          <div className="natural-profile-list">
            {naturalProfiles.map((profile) => {
              const pendingAnalysis = !profile.parsed_model && (!profile.criteria_json || Object.keys(profile.criteria_json).length === 0);
              return (
                <div className={`natural-profile-card${profile.is_active ? "" : " is-inactive"}`} key={profile.id}>
                  <div className="natural-profile-card-top">
                    <p className="natural-profile-name">{profile.name || "Recherche sans nom"}</p>
                    <div className="natural-profile-card-actions">
                      <button title="Éditer" onClick={() => setSelectedNaturalProfile(profile)}>
                        <Pencil size={14} />
                      </button>
                      <button title="Supprimer" onClick={() => deleteNaturalProfile(profile.id)}>
                        <X size={14} />
                      </button>
                    </div>
                  </div>
                  <p className="natural-profile-prompt">{profile.raw_prompt}</p>
                  <div className="natural-profile-card-footer">
                    <button
                      type="button"
                      className={`natural-profile-toggle${profile.is_active ? " is-active" : ""}`}
                      onClick={() => toggleNaturalProfileActive(profile)}
                    >
                      {profile.is_active ? "Active" : "Désactivée"}
                    </button>
                    {pendingAnalysis && <span className="natural-profile-pending">Analyse en attente</span>}
                  </div>
                </div>
              );
            })}
          </div>
        )}
        <form className="natural-profile-form" onSubmit={createNaturalProfile}>
          <input
            placeholder="Nom (optionnel)"
            value={naturalNameDraft}
            onChange={(event) => setNaturalNameDraft(event.target.value)}
          />
          <textarea
            placeholder="Décris ce que tu cherches, ex : 4 chambres minimum, piscine, clim, accès de plain-pied au jardin"
            value={naturalPromptDraft}
            onChange={(event) => setNaturalPromptDraft(event.target.value)}
          />
          <button className="primary" type="submit">
            <Plus size={18} />
            Ajouter cette recherche
          </button>
        </form>
      </section>
          </>
        )}
      </section>

      <section className="filters-row">
        <div className="filters">
          {FILTERS.map(([value, label]) => (
            <button key={value} className={status === value ? "active" : ""} onClick={() => setStatus(value)}>
              {label}
            </button>
          ))}
        </div>
        <div className="view-toggle" role="group" aria-label="Mode d'affichage">
          <button
            type="button"
            className={viewMode === "list" ? "active" : ""}
            onClick={() => setViewMode("list")}
            title="Vue liste"
          >
            <List size={15} />
            <span className="btn-label">Liste</span>
          </button>
          <button
            type="button"
            className={viewMode === "map" ? "active" : ""}
            onClick={() => setViewMode("map")}
            title="Vue carte"
          >
            <MapIcon size={15} />
            <span className="btn-label">Carte</span>
          </button>
        </div>
      </section>

      <div className="mobile-toolbar">
        <button type="button" className="mobile-filters-trigger" onClick={() => setShowFiltersSheet(true)}>
          <SlidersHorizontal size={15} />
          Filtres & tri
          {activeFilterCount > 0 && <span className="mobile-filters-count">{activeFilterCount}</span>}
        </button>
        <span className="mobile-toolbar-info">
          Tri : {SORT_LABELS[standardFilters.sort] || standardFilters.sort} · {filtered.length} annonce
          {filtered.length > 1 ? "s" : ""}
        </span>
      </div>

      {showFiltersSheet && <div className="sheet-backdrop" onClick={() => setShowFiltersSheet(false)} />}
      <section className={`standard-filters${showFiltersSheet ? " is-open" : ""}`} aria-label="Affiner l'affichage">
        <div className="standard-filters-title">
          <SlidersHorizontal size={17} />
          <span>Affiner l'affichage</span>
          <span className="standard-filters-hint">filtre temporaire, non enregistré</span>
          <button
            type="button"
            className="sheet-close"
            onClick={() => setShowFiltersSheet(false)}
            aria-label="Fermer les filtres"
          >
            <X size={18} />
          </button>
        </div>
        {availableCities.length > 1 && (
          <div className="city-filter">
            <span className="field-label">Ville</span>
            <div className="toggle-chips">
              {availableCities.map((city) => {
                const isActive = standardFilters.cities.includes(city);
                return (
                  <button
                    key={city}
                    type="button"
                    className={`toggle-chip${isActive ? " is-active" : ""}`}
                    onClick={() =>
                      setStandardFilters({
                        ...standardFilters,
                        cities: isActive
                          ? standardFilters.cities.filter((value) => value !== city)
                          : [...standardFilters.cities, city],
                      })
                    }
                    aria-pressed={isActive}
                  >
                    <MapPin size={14} />
                    {city}
                  </button>
                );
              })}
            </div>
          </div>
        )}
        <div className="filters-grid">
          <label className="field">
            <span className="field-label">Budget max</span>
            <input
              placeholder="ex. 500 000"
              inputMode="numeric"
              value={standardFilters.max_price_eur}
              onChange={(event) => setStandardFilters({ ...standardFilters, max_price_eur: event.target.value })}
            />
          </label>
          <label className="field">
            <span className="field-label">Surface min (m²)</span>
            <input
              placeholder="ex. 100"
              inputMode="numeric"
              value={standardFilters.min_living_area_m2}
              onChange={(event) => setStandardFilters({ ...standardFilters, min_living_area_m2: event.target.value })}
            />
          </label>
          <label className="field">
            <span className="field-label">Terrain min (m²)</span>
            <input
              placeholder="ex. 500"
              inputMode="numeric"
              value={standardFilters.min_land_area_m2}
              onChange={(event) => setStandardFilters({ ...standardFilters, min_land_area_m2: event.target.value })}
            />
          </label>
          <label className="field">
            <span className="field-label">Chambres min</span>
            <input
              placeholder="ex. 3"
              inputMode="numeric"
              value={standardFilters.min_bedrooms}
              onChange={(event) => setStandardFilters({ ...standardFilters, min_bedrooms: event.target.value })}
            />
          </label>
        </div>
        <div className="filters-toggles">
          <div className="toggle-chips">
            <button
              type="button"
              className={`toggle-chip${standardFilters.price_dropped_only ? " is-active" : ""}`}
              onClick={() =>
                setStandardFilters({ ...standardFilters, price_dropped_only: !standardFilters.price_dropped_only })
              }
              aria-pressed={standardFilters.price_dropped_only}
            >
              <TrendingDown size={14} />
              Baisse de prix uniquement
            </button>
            <button
              type="button"
              className={`toggle-chip${standardFilters.include_off_market ? " is-active" : ""}`}
              onClick={() =>
                setStandardFilters({ ...standardFilters, include_off_market: !standardFilters.include_off_market })
              }
              aria-pressed={standardFilters.include_off_market}
            >
              <Archive size={14} />
              Inclure les annonces retirées
            </button>
          </div>
          <label className="sort-control">
            <span className="field-label">Trier par</span>
            <select
              value={standardFilters.sort}
              onChange={(event) => {
                sortTouchedRef.current = true;
                setStandardFilters({ ...standardFilters, sort: event.target.value });
              }}
            >
              <option value="score">Score</option>
              <option value="match">Pertinence (IA)</option>
              <option value="price">Prix croissant</option>
              <option value="surface">Surface décroissante</option>
              <option value="updated">Plus récentes</option>
            </select>
          </label>
        </div>
        <div className="filters-footer">
          <button
            type="button"
            className="ghost compact"
            onClick={() => {
              sortTouchedRef.current = false;
              setStandardFilters({ ...DEFAULT_STANDARD_FILTERS, sort: defaultSort });
            }}
          >
            Réinitialiser
          </button>
          <button type="button" className="primary sheet-apply" onClick={() => setShowFiltersSheet(false)}>
            Voir {filtered.length} annonce{filtered.length > 1 ? "s" : ""}
          </button>
        </div>
      </section>

      <section className="summary">
        <div>
          <strong>{filtered.length}</strong>
          <span>Affichées</span>
        </div>
        <div>
          <strong>{listings.length}</strong>
          <span>Compatibles profils</span>
        </div>
        <div>
          <strong>{runs[0]?.found_count ?? 0}</strong>
          <span>Trouvées au dernier scan</span>
        </div>
        {newListingsCount > 0 && (
          <div className="summary-new">
            <strong>{newListingsCount}</strong>
            <span>Nouvelles</span>
          </div>
        )}
      </section>

      {initialLoading ? (
        <section className="grid" aria-busy="true" aria-label="Chargement des annonces">
          {Array.from({ length: 6 }).map((_, index) => (
            <article className="card skeleton-card" key={`skeleton-${index}`}>
              <div className="skeleton-block skeleton-photo" />
              <div className="skeleton-content">
                <div className="skeleton-block skeleton-line skeleton-line-title" />
                <div className="skeleton-block skeleton-line skeleton-line-short" />
                <div className="skeleton-block skeleton-line skeleton-line-price" />
                <div className="skeleton-block skeleton-line" />
                <div className="skeleton-block skeleton-line skeleton-line-short" />
              </div>
            </article>
          ))}
        </section>
      ) : filtered.length === 0 ? (
        <div className="empty-state">
          <span className="empty-state-icon">
            <Search size={24} />
          </span>
          <h3>{(EMPTY_STATE_COPY[status] || EMPTY_STATE_COPY.all).title}</h3>
          <p>{(EMPTY_STATE_COPY[status] || EMPTY_STATE_COPY.all).body}</p>
          {listings.length === 0 && (
            <div className="scan-action empty-scan-action">
              <button className="primary" onClick={runAllCrawlers} disabled={loading}>
                {loading ? <Loader2 size={18} className="spin" /> : <Search size={18} />}
                {loading
                  ? scanStage === "dedup"
                    ? "Déduplication..."
                    : scanStage === "ai"
                    ? "Analyse IA..."
                    : "Récupération..."
                  : "Lancer un scan"}
              </button>
              <ScanPipeline activeStage={scanStage} />
            </div>
          )}
        </div>
      ) : viewMode === "map" ? (
        <section className="map-view">
          <div className="map-container" ref={mapContainerRef} />
          {geoListings.length === 0 && (
            <div className="map-empty-overlay">
              <MapIcon size={22} />
              <p>Aucune annonce géolocalisée pour l'instant (les coordonnées arrivent au fil des scans Bien'ici).</p>
            </div>
          )}
        </section>
      ) : (
        <section className="grid">
          {filtered.map((listing) => (
            <article className={`card${listing.off_market ? " is-off-market" : ""}`} key={listing.id}>
              <div className="photo" onClick={() => openListing(listing)}>
                {listing.photos[0] ? <img src={listing.photos[0].url} alt={listing.title} /> : <Home size={44} />}
                {/* Un seul badge d'état à gauche : "Retirée" prime sur "Nouveau".
                    La source et la baisse de prix vivent dans le contenu de la carte. */}
                {listing.off_market ? (
                  <span className="off-market-badge" title="Annonce retirée du marché">
                    <Archive size={12} />
                    Retirée
                  </span>
                ) : (
                  listing.is_new === true && (
                    <span className="new-badge" title="Nouvelle annonce">
                      <Sparkle size={12} />
                      Nouveau
                    </span>
                  )
                )}
                <span className={`score-badge ${scoreTier(listing.score)}`}>{listing.score ?? "-"} / 100</span>
                {listing.match_score !== null && listing.match_score !== undefined && (
                  <span className="match-badge" title="Pertinence par rapport à ta recherche IA">
                    <Target size={12} />
                    {listing.match_score}
                  </span>
                )}
              </div>
              <div className="content">
                <h2 onClick={() => openListing(listing)}>{listing.title}</h2>
                <p className="location">
                  <MapPin size={14} />
                  {listing.city} {listing.postal_code || ""}
                </p>
                <p className="price">
                  {formatPrice(listing.price_eur)}
                  {formatPricePerM2(listing) && <span className="price-per-m2">{formatPricePerM2(listing)}</span>}
                  {listing.price_dropped && (
                    <span className="price-drop-badge price-drop-badge-inline" title="Le prix de cette annonce a baissé">
                      <TrendingDown size={12} />
                      {formatSignedAmount(listing.price_change_abs) && listing.price_change_abs < 0
                        ? formatSignedAmount(listing.price_change_abs)
                        : "Baisse"}
                    </span>
                  )}
                </p>
                {Array.isArray(listing.auto_flags) && listing.auto_flags.length > 0 && (
                  <div className="auto-flags-row">
                    {listing.auto_flags.slice(0, 3).map((flag, index) => (
                      <span
                        key={`${flag.code || flag.label}-${index}`}
                        className={`auto-flag-chip ${flag.severity === "warn" ? "is-warn" : "is-info"}`}
                        title={flag.label}
                      >
                        {flag.severity === "warn" ? <AlertTriangle size={11} /> : <Info size={11} />}
                        {flag.label}
                      </span>
                    ))}
                    {listing.auto_flags.length > 3 && (
                      <span className="auto-flag-chip is-more">+{listing.auto_flags.length - 3}</span>
                    )}
                  </div>
                )}
                <AIFeatureBadges features={listing.ai_features} />
                <p className="meta">
                  <span className="meta-item">
                    <Ruler size={14} />
                    {listing.living_area_m2 || "?"} m² hab.
                  </span>
                  <span className="meta-item">
                    <LandPlot size={14} />
                    {listing.land_area_m2 || "?"} m² terrain
                  </span>
                  <span className="meta-item">
                    <BedDouble size={14} />
                    {listing.bedrooms || "?"} ch.
                  </span>
                </p>
                <p className="description">{listing.description}</p>
                <div className="card-footer">
                  {listing.note && <p className="note-preview">{listing.note}</p>}
                  <div className="badges-row">
                    {listing.sources.slice(0, 3).map((src) => (
                      <SourceBadge key={`${src.source}-${src.url}`} src={src} onClick={(event) => event.stopPropagation()} />
                    ))}
                    {listing.sources.length > 3 && (
                      <span className="source-badge">+{listing.sources.length - 3}</span>
                    )}
                  </div>
                  <div className="actions">
                    <button
                      className={`action-favorite${listing.status === "favorite" ? " is-active" : ""}`}
                      title="Shortlist"
                      onClick={() => setListingStatus(listing.id, "favorite")}
                    >
                      <Heart size={18} />
                    </button>
                    <button
                      className={`action-call${listing.status === "call" ? " is-active" : ""}`}
                      title="A appeler"
                      onClick={() => setListingStatus(listing.id, "call")}
                    >
                      <Phone size={18} />
                    </button>
                    <button
                      className={`action-reject${listing.status === "rejected" ? " is-active" : ""}`}
                      title="Rejeter"
                      onClick={() => setListingStatus(listing.id, "rejected")}
                    >
                      <X size={18} />
                    </button>
                    <button
                      className={`action-compare${comparisonIds.has(listing.id) ? " is-active" : ""}`}
                      title={comparisonIds.has(listing.id) ? "Retirer du comparatif" : "Ajouter au comparatif"}
                      onClick={() => toggleComparison(listing.id)}
                    >
                      <Scale size={18} />
                    </button>
                  </div>
                </div>
              </div>
            </article>
          ))}
        </section>
      )}

      {(sourcesStatus.length > 0 || aggregatedSources.length > 0) && (
        <footer className="sources-strip" aria-label="Portails agrégés">
          <span className="sources-strip-label">Sources agrégées</span>
          <div className="sources-strip-items">
            {(sourcesStatus.length > 0
              ? sourcesStatus
              : aggregatedSources.map((source) => ({ source }))
            ).map((st) => {
              const tone = st.job_status
                ? "is-scanning"
                : st.last_status === "error"
                  ? "is-error"
                  : st.overdue
                    ? "is-overdue"
                    : "";
              const tooltip = [
                st.job_status === "running" ? "Scan en cours" : st.job_status === "pending" ? "Scan programmé, en attente d'exécuteur" : null,
                st.last_run_at
                  ? `Dernier scan ${relativeTimeFr(st.last_run_at)} (${st.last_found_count ?? "?"} annonces vues)`
                  : "Pas encore de scan enregistré",
                st.last_status === "error" && !st.job_status ? `En erreur : ${st.last_error || "erreur inconnue"}` : null,
                st.next_expected_at && !st.job_status ? `Prochain passage estimé ~${formatClockFr(st.next_expected_at)}` : null,
              ]
                .filter(Boolean)
                .join(" · ");
              return (
                <span className={`sources-strip-item ${tone}`} key={st.source} title={tooltip}>
                  <SourceLogo source={st.source} size={16} />
                  <span className="sources-strip-name">{sourceMeta(st.source).label}</span>
                  {typeof st.listings_count === "number" && (
                    <span className="sources-strip-count">{st.listings_count}</span>
                  )}
                  {(st.job_status || st.last_run_at) && (
                    <span className="sources-strip-freshness">
                      <span className="status-dot" aria-hidden="true" />
                      {st.job_status === "running"
                        ? "scan en cours…"
                        : st.job_status === "pending"
                          ? "programmé…"
                          : relativeTimeFr(st.last_run_at)}
                    </span>
                  )}
                </span>
              );
            })}
          </div>
          {sourcesStatus.some((st) => st.next_expected_at && !st.overdue && st.last_status !== "error") && (
            <span className="sources-strip-next">
              Prochain scan estimé ~
              {formatClockFr(
                sourcesStatus
                  .filter((st) => st.next_expected_at && !st.overdue)
                  .map((st) => st.next_expected_at)
                  .sort()[0]
              )}
            </span>
          )}
        </footer>
      )}

      {activeListing && (
        <div className="modal-backdrop" onClick={() => setSelectedListing(null)}>
          <section className="modal" onClick={(event) => event.stopPropagation()}>
            <button className="modal-close" title="Fermer" aria-label="Fermer" onClick={() => setSelectedListing(null)}>
              <X size={18} />
            </button>
            <div className="detail-photos">
              {(activeListing.photos.length ? activeListing.photos : [{ url: "" }]).slice(0, 4).map((photo, index) => (
                <div className="detail-photo" key={`${photo.url}-${index}`}>
                  {photo.url ? <img src={photo.url} alt="" /> : <Home size={36} />}
                </div>
              ))}
            </div>
            <h2>
              {activeListing.title}
              {activeListing.off_market && (
                <span className="off-market-badge off-market-badge-inline" title="Annonce retirée du marché">
                  <Archive size={12} />
                  Retirée
                </span>
              )}
              {activeListing.is_new === true && (
                <span className="new-badge new-badge-inline" title="Nouvelle annonce">
                  <Sparkle size={12} />
                  Nouveau
                </span>
              )}
            </h2>
            <p className="location">
              <MapPin size={16} />
              {activeListing.city} {activeListing.postal_code || ""}
            </p>
            <p className="price">
              {formatPrice(activeListing.price_eur)}
              {formatPricePerM2(activeListing) && <span className="price-per-m2">{formatPricePerM2(activeListing)}</span>}
              {activeListing.price_dropped && (
                <span className="price-drop-badge price-drop-badge-inline" title="Le prix de cette annonce a baissé">
                  <TrendingDown size={12} />
                  Baisse de prix
                  {formatSignedAmount(activeListing.price_change_abs) && activeListing.price_change_abs < 0 && (
                    <span className="price-drop-amount">{formatSignedAmount(activeListing.price_change_abs)}</span>
                  )}
                </span>
              )}
            </p>
            <p className="meta">
              <span className="meta-item">
                <Ruler size={14} />
                {activeListing.living_area_m2 || "?"} m² hab.
              </span>
              <span className="meta-item">
                <LandPlot size={14} />
                {activeListing.land_area_m2 || "?"} m² terrain
              </span>
              <span className="meta-item">
                <Building2 size={14} />
                {activeListing.rooms || "?"} pièces
              </span>
              <span className="meta-item">
                <BedDouble size={14} />
                {activeListing.bedrooms || "?"} ch.
              </span>
              {daysOnMarketLabel(activeListing) && (
                <span className="meta-item meta-item-muted">
                  <Archive size={14} />
                  {daysOnMarketLabel(activeListing)}
                </span>
              )}
            </p>
            {/* Actions principales en tête de fiche : décider ne doit pas
                exiger de scroller toute l'analyse. */}
            <div className="modal-quick-actions">
              <button
                className={`action-favorite${activeListing.status === "favorite" ? " is-active" : ""}`}
                onClick={() => setListingStatus(activeListing.id, "favorite", noteDraft)}
              >
                <Heart size={16} />
                Shortlist
              </button>
              <button
                className={`action-call${activeListing.status === "call" ? " is-active" : ""}`}
                onClick={() => setListingStatus(activeListing.id, "call", noteDraft)}
              >
                <Phone size={16} />
                A appeler
              </button>
              <button
                className={`action-reject${activeListing.status === "rejected" ? " is-active" : ""}`}
                onClick={() => setListingStatus(activeListing.id, "rejected", noteDraft)}
              >
                <X size={16} />
                Rejeter
              </button>
              <button
                className={`action-compare${comparisonIds.has(activeListing.id) ? " is-active" : ""}`}
                onClick={() => toggleComparison(activeListing.id)}
              >
                <Scale size={16} />
                {comparisonIds.has(activeListing.id) ? "Comparé" : "Comparer"}
              </button>
            </div>
            {activeListing.sources.length > 0 && (
              <div className="modal-sources">
                <span className="modal-sources-label">Publiée sur</span>
                {activeListing.sources.map((src) => (
                  <SourceBadge key={`${src.source}-${src.url}`} src={src} />
                ))}
              </div>
            )}
            <p className="modal-description">{activeListing.description}</p>

            {!priceHistoryLoading && priceHistory.length >= 2 && (
              <div className="price-history-block">
                <h3>
                  <TrendingDown size={14} />
                  Évolution du prix
                </h3>
                <PriceSparkline points={priceHistory} />
                <ul className="price-history-list">
                  {priceHistory.map((point, index) => (
                    <li key={`${point.observed_at}-${index}`}>
                      <span className="price-history-date">{formatShortDate(point.observed_at)}</span>
                      <span className="price-history-value">{formatPrice(point.price_eur)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {Array.isArray(activeListing.auto_flags) && activeListing.auto_flags.length > 0 && (
              <div className="auto-flags-block">
                <h3>
                  <Flag size={14} />
                  Signaux automatiques
                  <span className="auto-flags-block-hint">détection instantanée</span>
                </h3>
                <ul>
                  {activeListing.auto_flags.map((flag, index) => (
                    <li key={`${flag.code || flag.label}-${index}`} className={flag.severity === "warn" ? "is-warn" : "is-info"}>
                      {flag.severity === "warn" ? <AlertTriangle size={13} /> : <Info size={13} />}
                      {flag.label}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div className="market-risks-block">
              <h3>
                <ShieldAlert size={14} />
                Marché &amp; risques
              </h3>
              {activeListing.dvf_median_price_per_m2 ? (
                <div className="market-dvf">
                  <p className="market-dvf-line">
                    Ventes réelles : ~{formatPricePerM2Value(activeListing.dvf_median_price_per_m2)}
                    {activeListing.dvf_period ? ` (médiane ${activeListing.dvf_period})` : ""}
                  </p>
                  {formatPricePerM2(activeListing) && (
                    <p className={`market-dvf-line market-dvf-delta is-${dvfDeltaTone(activeListing.dvf_delta_ratio)}`}>
                      Ce bien : {formatPricePerM2(activeListing)}
                      {formatSignedPercent(activeListing.dvf_delta_ratio) && (
                        <span className="market-dvf-delta-value">
                          ({formatSignedPercent(activeListing.dvf_delta_ratio)} vs ventes réelles)
                        </span>
                      )}
                    </p>
                  )}
                </div>
              ) : (
                <p className="market-dvf-line market-dvf-line-muted">Ventes réelles non disponibles pour cette ville</p>
              )}

              {activeListing.risks ? (
                presentRisks(activeListing.risks).length > 0 ? (
                  <ul className="risk-chip-list">
                    {presentRisks(activeListing.risks).map((risk) => (
                      <li className="risk-chip" key={risk.code}>
                        {risk.label}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="market-dvf-line market-dvf-line-muted">Aucun risque détecté (Géorisques)</p>
                )
              ) : (
                <p className="market-dvf-line market-dvf-line-muted">Risques non vérifiés (pas de coordonnées)</p>
              )}
            </div>

            {activeListing.ai_summary && (
              <div className="ai-summary">
                <h3>
                  <Sparkles size={14} />
                  Résumé IA
                </h3>
                <p>{activeListing.ai_summary}</p>
                <AIFeatureBadges features={activeListing.ai_features} detailed />
              </div>
            )}

            {activeListing.red_flags && activeListing.red_flags.length > 0 && (
              <div className="ai-red-flags">
                <h3>
                  <AlertTriangle size={14} />
                  Points d'attention
                </h3>
                <ul>
                  {activeListing.red_flags.map((flag, index) => (
                    <li key={`flag-${index}`}>{renderListItem(flag)}</li>
                  ))}
                </ul>
              </div>
            )}

            {activeListing.match_score !== null && activeListing.match_score !== undefined && (
              <div className="ai-match">
                <h3>
                  <Target size={14} />
                  Correspondance avec ta recherche
                  {activeListing.active_profile_name && (
                    <span className="ai-match-profile">{activeListing.active_profile_name}</span>
                  )}
                </h3>
                <div className="ai-match-score">
                  <span className={`score-badge ${scoreTier(activeListing.match_score)}`}>
                    {activeListing.match_score} / 100
                  </span>
                </div>
                {activeListing.match_reasons && activeListing.match_reasons.length > 0 && (
                  <div className="ai-match-group ai-match-good">
                    <p className="ai-match-group-title">Points forts</p>
                    <ul>
                      {activeListing.match_reasons.map((item, index) => (
                        <li key={`reason-${index}`}>{renderListItem(item)}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {activeListing.match_missing && activeListing.match_missing.length > 0 && (
                  <div className="ai-match-group ai-match-warn">
                    <p className="ai-match-group-title">À vérifier</p>
                    <ul>
                      {activeListing.match_missing.map((item, index) => (
                        <li key={`missing-${index}`}>{renderListItem(item)}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {activeListing.match_dealbreakers && activeListing.match_dealbreakers.length > 0 && (
                  <div className="ai-match-group ai-match-bad">
                    <p className="ai-match-group-title">Points bloquants</p>
                    <ul>
                      {activeListing.match_dealbreakers.map((item, index) => (
                        <li key={`dealbreaker-${index}`}>{renderListItem(item)}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}

            {activeListing.score_breakdown && activeListing.score_breakdown.length > 0 && (
              <details className="score-breakdown">
                <summary>Détail du score ({activeListing.score ?? "-"} / 100)</summary>
                <ul>
                  {activeListing.score_breakdown.map((factor, index) => (
                    <li key={`${factor.label}-${index}`} className={factor.delta < 0 ? "malus" : "bonus"}>
                      <span className="factor-label">{factor.label}</span>
                      <span className="factor-delta">
                        {factor.delta > 0 ? `+${factor.delta}` : factor.delta}
                      </span>
                    </li>
                  ))}
                </ul>
              </details>
            )}
            <div className="note-field">
              <label className="field-label" htmlFor="listing-note">
                Notes privées
              </label>
              <textarea
                id="listing-note"
                placeholder="Ajoute une note pour le groupe (visite prévue, points d'attention...)"
                value={noteDraft}
                onChange={(event) => setNoteDraft(event.target.value)}
              />
            </div>
            <div className="modal-actions">
              <button className="action-save" onClick={() => saveNote(activeListing.id, noteDraft)}>
                <Save size={18} />
                Enregistrer la note
              </button>
            </div>
          </section>
        </div>
      )}

      {selectedProfile && (
        <div className="modal-backdrop" onClick={() => setSelectedProfile(null)}>
          <section className="modal small" onClick={(event) => event.stopPropagation()}>
            <button className="modal-close" title="Fermer" aria-label="Fermer" onClick={() => setSelectedProfile(null)}>
              <X size={18} />
            </button>
            <h2>
              <MapPin size={18} style={{ verticalAlign: "-3px", marginRight: 6, color: "var(--color-brand)" }} />
              {selectedProfile.city}
            </h2>
            <label className="field-label" htmlFor="profile-max-price">
              Budget max
            </label>
            <input
              id="profile-max-price"
              inputMode="numeric"
              value={selectedProfile.max_price_eur || ""}
              onChange={(event) => setSelectedProfile({ ...selectedProfile, max_price_eur: event.target.value })}
            />
            <label className="field-label" htmlFor="profile-living-area">
              Surface habitable min
            </label>
            <input
              id="profile-living-area"
              inputMode="numeric"
              value={selectedProfile.min_living_area_m2 || ""}
              onChange={(event) => setSelectedProfile({ ...selectedProfile, min_living_area_m2: event.target.value })}
            />
            <label className="field-label" htmlFor="profile-land-area">
              Terrain min
            </label>
            <input
              id="profile-land-area"
              inputMode="numeric"
              value={selectedProfile.min_land_area_m2 || ""}
              onChange={(event) => setSelectedProfile({ ...selectedProfile, min_land_area_m2: event.target.value })}
            />
            <label className="field-label" htmlFor="profile-bedrooms">
              Chambres min
            </label>
            <input
              id="profile-bedrooms"
              inputMode="numeric"
              value={selectedProfile.min_bedrooms || ""}
              onChange={(event) => setSelectedProfile({ ...selectedProfile, min_bedrooms: event.target.value })}
            />
            <button className="primary" onClick={() => saveProfile(selectedProfile)}>
              <Save size={18} />
              Enregistrer
            </button>
          </section>
        </div>
      )}

      {selectedNaturalProfile && (
        <div className="modal-backdrop" onClick={() => setSelectedNaturalProfile(null)}>
          <section className="modal small" onClick={(event) => event.stopPropagation()}>
            <button className="modal-close" title="Fermer" aria-label="Fermer" onClick={() => setSelectedNaturalProfile(null)}>
              <X size={18} />
            </button>
            <h2>
              <Sparkles size={18} style={{ verticalAlign: "-3px", marginRight: 6, color: "var(--color-brand)" }} />
              Recherche en langage naturel
            </h2>
            <label className="field-label" htmlFor="natural-profile-name">
              Nom
            </label>
            <input
              id="natural-profile-name"
              value={selectedNaturalProfile.name || ""}
              onChange={(event) => setSelectedNaturalProfile({ ...selectedNaturalProfile, name: event.target.value })}
            />
            <label className="field-label" htmlFor="natural-profile-prompt">
              Description libre
            </label>
            <textarea
              id="natural-profile-prompt"
              value={selectedNaturalProfile.raw_prompt || ""}
              onChange={(event) => setSelectedNaturalProfile({ ...selectedNaturalProfile, raw_prompt: event.target.value })}
            />
            <label className="field-label" htmlFor="natural-profile-active">
              <input
                id="natural-profile-active"
                type="checkbox"
                style={{ width: "auto", marginRight: 8 }}
                checked={!!selectedNaturalProfile.is_active}
                onChange={(event) => setSelectedNaturalProfile({ ...selectedNaturalProfile, is_active: event.target.checked })}
              />
              Recherche active
            </label>
            <button className="primary" onClick={() => saveNaturalProfile(selectedNaturalProfile)}>
              <Save size={18} />
              Enregistrer
            </button>
          </section>
        </div>
      )}

      {showComparison && (
        <div className="modal-backdrop" onClick={() => setShowComparison(false)}>
          <section className="modal compare-modal" onClick={(event) => event.stopPropagation()}>
            <button className="modal-close" title="Fermer" aria-label="Fermer" onClick={() => setShowComparison(false)}>
              <X size={18} />
            </button>
            <h2>
              <Scale size={18} style={{ verticalAlign: "-3px", marginRight: 6, color: "var(--color-brand)" }} />
              Comparatif ({comparison.length}/{4})
            </h2>
            {comparison.length === 0 ? (
              <p className="compare-empty">
                Ajoute des annonces au comparatif depuis leurs cartes pour les retrouver ici.
              </p>
            ) : (
              <div className="compare-table-wrap">
                <table className="compare-table">
                  <thead>
                    <tr>
                      <th className="compare-row-label"></th>
                      {comparison.map((listing) => (
                        <th key={listing.id}>
                          <button
                            className="compare-remove"
                            title="Retirer du comparatif"
                            onClick={() => removeFromComparison(listing.id)}
                          >
                            <X size={14} />
                          </button>
                          <div className="compare-photo">
                            {listing.photos[0] ? <img src={listing.photos[0].url} alt={listing.title} /> : <Home size={26} />}
                          </div>
                          <p className="compare-title" onClick={() => openListing(listing)}>
                            {listing.title}
                          </p>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <th className="compare-row-label">Prix</th>
                      {comparison.map((listing) => (
                        <td key={listing.id} className="compare-price">
                          {formatPrice(listing.price_eur)}
                        </td>
                      ))}
                    </tr>
                    <tr>
                      <th className="compare-row-label">Prix / m²</th>
                      {comparison.map((listing) => (
                        <td key={listing.id}>{pricePerM2(listing)}</td>
                      ))}
                    </tr>
                    <tr>
                      <th className="compare-row-label">Surface habitable</th>
                      {comparison.map((listing) => (
                        <td key={listing.id}>{listing.living_area_m2 ? `${listing.living_area_m2} m²` : "?"}</td>
                      ))}
                    </tr>
                    <tr>
                      <th className="compare-row-label">Terrain</th>
                      {comparison.map((listing) => (
                        <td key={listing.id}>{listing.land_area_m2 ? `${listing.land_area_m2} m²` : "?"}</td>
                      ))}
                    </tr>
                    <tr>
                      <th className="compare-row-label">Chambres</th>
                      {comparison.map((listing) => (
                        <td key={listing.id}>{listing.bedrooms ?? "?"}</td>
                      ))}
                    </tr>
                    <tr>
                      <th className="compare-row-label">Ville</th>
                      {comparison.map((listing) => (
                        <td key={listing.id}>
                          {listing.city} {listing.postal_code || ""}
                        </td>
                      ))}
                    </tr>
                    <tr>
                      <th className="compare-row-label">Source</th>
                      {comparison.map((listing) => (
                        <td key={listing.id}>
                          {(() => {
                            const src = listing.sources.find(isSourceUrlUsable) || listing.sources[0];
                            if (!src) return "?";
                            const label = sourceMeta(src.source).label;
                            if (!isSourceUrlUsable(src)) {
                              return (
                                <span className="source" title={`Lien ${label} indisponible`}>
                                  <SourceLogo source={src.source} size={13} />
                                  {label}
                                </span>
                              );
                            }
                            return (
                              <a href={src.url} target="_blank" rel="noreferrer" className="source">
                                <SourceLogo source={src.source} size={13} />
                                {label}
                              </a>
                            );
                          })()}
                        </td>
                      ))}
                    </tr>
                    <tr>
                      <th className="compare-row-label">Score</th>
                      {comparison.map((listing) => (
                        <td key={listing.id}>
                          <span className={`score-badge ${scoreTier(listing.score)}`}>{listing.score ?? "-"} / 100</span>
                        </td>
                      ))}
                    </tr>
                    <tr>
                      <th className="compare-row-label">Note privée</th>
                      {comparison.map((listing) => (
                        <td key={listing.id} className="compare-note">
                          {listing.note || "—"}
                        </td>
                      ))}
                    </tr>
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </div>
      )}

      {showAdmin && (
        <div className="modal-backdrop" onClick={() => setShowAdmin(false)}>
          <section className="modal admin-modal" onClick={(event) => event.stopPropagation()}>
            <button className="modal-close" title="Fermer" aria-label="Fermer" onClick={() => setShowAdmin(false)}>
              <X size={18} />
            </button>
            <h2>
              <ShieldCheck size={18} style={{ verticalAlign: "-3px", marginRight: 6, color: "var(--color-brand)" }} />
              Administration
            </h2>
            {adminError && <p className="error">{adminError}</p>}
            {adminLoading ? (
              <p className="admin-loading">
                <Loader2 size={16} className="spin" />
                Chargement...
              </p>
            ) : (
              <>
                <div className="admin-section">
                  <h3>
                    <Users size={15} />
                    Utilisateurs
                  </h3>
                  {adminUsers.length === 0 ? (
                    <p className="admin-empty">Aucun utilisateur.</p>
                  ) : (
                    <div className="admin-table-wrap">
                      <table className="admin-table">
                        <thead>
                          <tr>
                            <th>Email</th>
                            <th>Nom</th>
                            <th>Inscrit le</th>
                            <th>Rôle</th>
                          </tr>
                        </thead>
                        <tbody>
                          {adminUsers.map((adminUser) => (
                            <tr key={adminUser.id}>
                              <td>{adminUser.email}</td>
                              <td>{adminUser.display_name || "—"}</td>
                              <td>{formatShortDate(adminUser.created_at)}</td>
                              <td>
                                {adminUser.is_admin ? (
                                  <span className="admin-badge">
                                    <UserCheck size={12} />
                                    Admin
                                  </span>
                                ) : (
                                  "—"
                                )}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                <div className="admin-section">
                  <h3>
                    <ShieldCheck size={15} />
                    Codes d'invitation
                  </h3>
                  {adminInviteCodes.length === 0 ? (
                    <p className="admin-empty">Aucun code généré pour l'instant.</p>
                  ) : (
                    <div className="admin-table-wrap">
                      <table className="admin-table">
                        <thead>
                          <tr>
                            <th>Code</th>
                            <th>Statut</th>
                            <th>Note</th>
                            <th>Utilisations</th>
                            <th></th>
                          </tr>
                        </thead>
                        <tbody>
                          {adminInviteCodes.map((code) => (
                            <tr key={code.id}>
                              <td>
                                <span className="admin-code">
                                  {code.code}
                                  <button
                                    type="button"
                                    className="admin-copy"
                                    title="Copier le code"
                                    onClick={() => navigator.clipboard?.writeText(code.code)}
                                  >
                                    <Copy size={13} />
                                  </button>
                                </span>
                              </td>
                              <td>
                                <span className={`admin-status${code.active ? " is-active" : ""}`}>
                                  {code.active ? "Actif" : "Inactif"}
                                </span>
                              </td>
                              <td>{code.note || "—"}</td>
                              <td>{code.used_count ?? 0}</td>
                              <td>
                                <button
                                  type="button"
                                  className="ghost compact"
                                  onClick={() => toggleInviteCodeActive(code)}
                                >
                                  {code.active ? "Désactiver" : "Activer"}
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  <form className="admin-generate-form" onSubmit={generateInviteCode}>
                    <input
                      placeholder="Note (optionnel)"
                      value={adminNoteDraft}
                      onChange={(event) => setAdminNoteDraft(event.target.value)}
                    />
                    <button className="primary" type="submit" disabled={adminGenerating}>
                      {adminGenerating ? <Loader2 size={16} className="spin" /> : <Plus size={16} />}
                      Générer un code
                    </button>
                  </form>
                </div>
              </>
            )}
          </section>
        </div>
      )}
    </main>
  );
}

function numberOrNull(value) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

createRoot(document.getElementById("root")).render(<App />);
