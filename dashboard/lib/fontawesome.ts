/**
 * FontAwesome bootstrap.
 * We disable auto-CSS injection because we import the core CSS manually in
 * globals.css (required for Next.js App Router / SSR to avoid icon flicker).
 */
import { config } from "@fortawesome/fontawesome-svg-core";

config.autoAddCss = false;
