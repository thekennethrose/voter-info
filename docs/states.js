const STATES = {
  AL: "Alabama", AK: "Alaska", AZ: "Arizona", AR: "Arkansas", CA: "California",
  CO: "Colorado", CT: "Connecticut", DE: "Delaware", FL: "Florida", GA: "Georgia",
  HI: "Hawaii", ID: "Idaho", IL: "Illinois", IN: "Indiana", IA: "Iowa",
  KS: "Kansas", KY: "Kentucky", LA: "Louisiana", ME: "Maine", MD: "Maryland",
  MA: "Massachusetts", MI: "Michigan", MN: "Minnesota", MS: "Mississippi", MO: "Missouri",
  MT: "Montana", NE: "Nebraska", NV: "Nevada", NH: "New Hampshire", NJ: "New Jersey",
  NM: "New Mexico", NY: "New York", NC: "North Carolina", ND: "North Dakota", OH: "Ohio",
  OK: "Oklahoma", OR: "Oregon", PA: "Pennsylvania", RI: "Rhode Island", SC: "South Carolina",
  SD: "South Dakota", TN: "Tennessee", TX: "Texas", UT: "Utah", VT: "Vermont",
  VA: "Virginia", WA: "Washington", WV: "West Virginia", WI: "Wisconsin", WY: "Wyoming",
  DC: "District of Columbia", PR: "Puerto Rico", GU: "Guam", AS: "American Samoa",
  VI: "U.S. Virgin Islands", MP: "Northern Mariana Islands",
};
const PARTIES = { D: "Democrat", R: "Republican", I: "Independent" };

function ordinal(n) {
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

// Non-voting House members elected from D.C. and the territories.
const TERRITORY_OFFICE = { DC: "Delegate", GU: "Delegate", AS: "Delegate", VI: "Delegate", MP: "Delegate", PR: "Resident Commissioner" };

// "Senator", "Representative", "Delegate" or "Resident Commissioner"
function office(m) {
  if (m.chamber === "senate") return "Senator";
  return TERRITORY_OFFICE[m.state] ?? "Representative";
}

// "Senator", "Representative, 5th District", "Representative, at large", "Delegate"
function role(m) {
  const o = office(m);
  if (o !== "Representative") return o;
  return m.district ? `Representative, ${ordinal(m.district)} District` : "Representative, at large";
}
