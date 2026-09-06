/**
 * Rule-based agent debate lines triggered by user policy edits (not backend autoplay).
 */
import type { Locale } from "@/lib/i18n/types";
import type { EditMeta, TariffMeta } from "@/lib/worldModel";

export interface PolicyAgentLine {
  id: string;
  ts: string;
  simDay?: number;
  actor: string;
  country: string;
  text: string;
  level: "info" | "ok" | "warn" | "danger";
  source: "policy";
}

export interface PolicyEditContext {
  locale: Locale;
  simDay?: number;
  country: string;
  labelKey: string;
  fraction: boolean;
  newValue: number;
  meta: EditMeta;
}

export interface TariffEditContext {
  locale: Locale;
  simDay?: number;
  meta: TariffMeta;
}

function pct(v: number, fraction: boolean): string {
  return fraction ? `${(v * 100).toFixed(1)}%` : v.toFixed(2);
}

function featureLabel(locale: Locale, key: string): string {
  const vi: Record<string, string> = {
    "fiscal.individual_tax": "thuế thu nhập cá nhân",
    "fiscal.vat": "VAT",
    "fiscal.corporate_tax": "thuế doanh nghiệp",
    "monetary.interest_rate": "lãi suất chính sách",
    "monetary.inflation_cpi": "lạm phát CPI",
    "geopolitical.social_unrest_index": "chỉ số bất ổn xã hội",
  };
  const en: Record<string, string> = {
    "fiscal.individual_tax": "personal income tax",
    "fiscal.vat": "VAT",
    "fiscal.corporate_tax": "corporate tax",
    "monetary.interest_rate": "policy interest rate",
    "monetary.inflation_cpi": "CPI inflation",
    "geopolitical.social_unrest_index": "social unrest index",
  };
  return (locale === "vi" ? vi : en)[key] ?? key;
}

function line(
  ctx: { locale: Locale; simDay?: number },
  actor: string,
  country: string,
  text: string,
  level: PolicyAgentLine["level"] = "warn",
): Omit<PolicyAgentLine, "id"> {
  return {
    ts: new Date().toISOString(),
    simDay: ctx.simDay,
    actor,
    country,
    text,
    level,
    source: "policy",
  };
}

const SOCIETAL_FIELDS = new Set([
  "fiscal.individual_tax",
  "fiscal.vat",
  "geopolitical.social_unrest_index",
]);

const GOVERNMENT_FIELDS = new Set([
  "monetary.interest_rate",
  "monetary.inflation_cpi",
  "fiscal.corporate_tax",
  "fiscal.vat",
]);

const CORPORATE_FIELDS = new Set([
  "fiscal.export_index",
  "fiscal.trade_balance_pct",
  "industrial.manufacturing_pmi",
]);

export function buildPolicyEditReactions(ctx: PolicyEditContext): Omit<PolicyAgentLine, "id">[] {
  const { locale, country, labelKey, fraction, newValue, meta } = ctx;
  const val = pct(newValue, fraction);
  const label = featureLabel(locale, labelKey);
  const out: Omit<PolicyAgentLine, "id">[] = [];
  const up = meta.delta > 0;

  if (SOCIETAL_FIELDS.has(labelKey)) {
    if (locale === "vi") {
      out.push(
        line(
          ctx,
          "Societal AI",
          country,
          up
            ? `Thuế tăng lên ${val} — người dân phản ứng mạnh, chỉ số bất ổn xã hội đã được cập nhật trong mô hình.`
            : `Giảm ${label} xuống ${val} — áp lực đời sống giảm, tâm lý tiêu dùng cải thiện.`,
          up ? "danger" : "ok",
        ),
      );
    } else {
      out.push(
        line(
          ctx,
          "Societal AI",
          country,
          up
            ? `Tax hike to ${val} — households push back; social unrest index updated in the sim.`
            : `${label} cut to ${val} — living-cost pressure eases and consumer sentiment improves.`,
          up ? "danger" : "ok",
        ),
      );
    }
  }

  if (GOVERNMENT_FIELDS.has(labelKey)) {
    if (locale === "vi") {
      out.push(
        line(
          ctx,
          "Government AI",
          country,
          meta.tier === "hub"
            ? `Trung tâm lõi điều chỉnh ${label} thành ${val}; chúng tôi theo dõi lan truyền sang các proxy phụ thuộc.`
            : `Chính sách ${label} được đặt thành ${val}, ghi đè tương quan khu vực.`,
        ),
      );
    } else {
      out.push(
        line(
          ctx,
          "Government AI",
          country,
          meta.tier === "hub"
            ? `Core hub moved ${label} to ${val}; monitoring spillovers to dependent proxies.`
            : `${label} set to ${val}, overriding regional correlation.`,
        ),
      );
    }
  }

  if (CORPORATE_FIELDS.has(labelKey)) {
    if (locale === "vi") {
      out.push(
        line(
          ctx,
          "Corporate AI",
          country,
          up
            ? `Doanh nghiệp điều chỉnh kế hoạch đầu tư sau khi ${label} tăng lên ${val}.`
            : `Tín hiệu ${label} giảm — kỳ vọng biên lợi nhuận và xuất khẩu được nới lỏng.`,
        ),
      );
    } else {
      out.push(
        line(
          ctx,
          "Corporate AI",
          country,
          up
            ? `Firms revise capex plans after ${label} rose to ${val}.`
            : `Lower ${label} at ${val} — margin and export outlook improves.`,
        ),
      );
    }
  }

  for (const proxy of meta.affectedProxies.slice(0, 2)) {
    const proxyUp = proxy.move > 0;
    if (locale === "vi") {
      out.push(
        line(
          ctx,
          "Government AI",
          proxy.code,
          proxyUp
            ? `${label} tại ${proxy.code} tăng theo sóng từ ${country} (Hub→Proxy) — chúng tôi cân nhắc đối ứng tài khóa.`
            : `${label} tại ${proxy.code} giảm theo điều chỉnh của ${country}; cần giữ ổn định tài chính.`,
        ),
      );
    } else {
      out.push(
        line(
          ctx,
          "Government AI",
          proxy.code,
          proxyUp
            ? `${label} in ${proxy.code} rose on spillover from ${country} (Hub→Proxy) — fiscal response under review.`
            : `${label} in ${proxy.code} eased with ${country}'s move; prioritising stability.`,
        ),
      );
    }
  }

  return out;
}

export function buildTariffReactions(ctx: TariffEditContext): Omit<PolicyAgentLine, "id">[] {
  const { locale, meta } = ctx;
  const { src, dst, rate, delta } = meta;
  if (Math.abs(delta) < 1e-4) return [];
  const pctRate = `${(rate * 100).toFixed(0)}%`;
  const out: Omit<PolicyAgentLine, "id">[] = [];
  const raised = delta > 0;

  if (locale === "vi") {
    out.push(
      line(
        ctx,
        "Government AI",
        src,
        raised
          ? `Áp thuế nhập khẩu ${pctRate} lên ${dst} — bảo vệ sản xuất nội địa, chấp nhận CPI nhập khẩu tăng.`
          : `Giảm thuế quan với ${dst} xuống ${pctRate} để hạ chi phí đầu vào.`,
        raised ? "warn" : "ok",
      ),
      line(
        ctx,
        "Corporate AI",
        dst,
        raised
          ? `Thuế từ ${src} đẩy chi phí xuất khẩu — chỉ số xuất khẩu và niềm tin doanh nghiệp đã giảm trong mô hình.`
          : `Nới thuế từ ${src} — kỳ vọng phục hồi đơn hàng và sản xuất.`,
        raised ? "danger" : "ok",
      ),
      line(
        ctx,
        "Societal AI",
        dst,
        raised
          ? `Giá hàng nhập từ ${src} có thể tăng; người tiêu dùng ${dst} lo ngại lạm phát.`
          : `Hàng hóa từ ${src} rẻ hơn — áp lực giá tiêu dùng giảm.`,
      ),
    );
  } else {
    out.push(
      line(
        ctx,
        "Government AI",
        src,
        raised
          ? `Import tariff on ${dst} set to ${pctRate} — shielding domestic industry, accepting higher import CPI.`
          : `Tariff relief on ${dst} at ${pctRate} to lower input costs.`,
        raised ? "warn" : "ok",
      ),
      line(
        ctx,
        "Corporate AI",
        dst,
        raised
          ? `Duties from ${src} hit export margins — export index and business confidence already down in the sim.`
          : `Tariff cut from ${src} — order books and output expected to recover.`,
        raised ? "danger" : "ok",
      ),
      line(
        ctx,
        "Societal AI",
        dst,
        raised
          ? `Imported goods from ${src} may cost more; households in ${dst} fear inflation.`
          : `Cheaper imports from ${src} — consumer price pressure eases.`,
      ),
    );
  }

  return out;
}
