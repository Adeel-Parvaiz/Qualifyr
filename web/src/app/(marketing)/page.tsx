import { Hero } from "@/components/marketing/hero"
import { StatsBand } from "@/components/marketing/stats-band"
import { FeaturesBento } from "@/components/marketing/features-bento"
import { HowItWorks } from "@/components/marketing/how-it-works"
import { LeadToCash } from "@/components/marketing/lead-to-cash"
import { DeviceMockups } from "@/components/marketing/device-mockups"
import { ComparisonTable } from "@/components/marketing/comparison-table"
import { Faq } from "@/components/marketing/faq"
import { FinalCta } from "@/components/marketing/final-cta"

export default function LandingPage() {
  return (
    <>
      <Hero />
      <StatsBand />
      <FeaturesBento />
      <HowItWorks />
      <LeadToCash />
      <DeviceMockups />
      <ComparisonTable />
      <Faq />
      <FinalCta />
    </>
  )
}
