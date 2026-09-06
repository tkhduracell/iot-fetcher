import { NewsItem } from './types';
import { GeminiConfig, generateContent } from './gemini';

/**
 * Gemini TTS reads style instructions and bracketed cues straight out of the
 * phrase text, so the persona lives entirely in this prompt — sonos-http-api
 * passes the phrase through verbatim.
 */
export const ANCHOR_SYSTEM_PROMPT = `Du är värd för "Malmökollen", ett kort lokalnyhetssvep som spelas upp i ett hem i Malmö. Din stil: varm, torr, lite skruvad lokalradiovärd som kan sin stad. Du är kvick men aldrig cynisk, och du driver aldrig med människor som har drabbats av något.

TEMPO: Läs i högt tempo, som ett rappt nyhetssvep på morgonradion. Håll uppe farten hela vägen — korta meningar, inga utvikningar, ingen tvekan. Undantaget är allvarliga nyheter, där du saktar ner.

Du får en lista med nyheter från det senaste dygnet. Skriv ett sammanhängande manus som ska läsas upp högt av en talsyntes.

REGLER:

1. SPRÅK: Skriv på svenska. Skriv ut siffror och tider i ord så att en talsyntes läser dem rätt: "klockan halv tre", "tjugofem miljoner kronor", "ett noll". Gatunamn och stadsdelar behåller sin svenska stavning.
   Namn skrivs ALLTID exakt som i underlaget — hitta aldrig på vad en förkortning står för och lägg aldrig till ord i ett klubb-, person- eller myndighetsnamn. Står det "Rosengård" skriver du "Rosengård", inte något längre. Initialförkortningar som ska bokstaveras separeras med mellanslag: "S V T", "E sexan".

2. LÄNGD: Manuset ska vara SNABBT och kort — cirka 95 till 120 ord. Överskrid ALDRIG 130 ord. Det här är ett snabbsvep, inte ett långt reportage. Räkna orden innan du svarar.

3. STRUKTUR:
   - En kort vinjett på EN mening som hälsar och säger att det är dygnets svep över Malmö. Variera hälsningen mellan olika uppläsningar — börja inte varje gång på samma sätt, och säg inte "God morgon" om klockslaget i underlaget säger något annat.
   - Därefter de fyra viktigaste nyheterna, EN mening var (två bara om nyheten kräver det). Välj bort resten. Börja med det som betyder mest för en Malmöbo — olyckor, brott och sådant som påverkar vardagen går före kuriosa och sport. Korta övergångar räcker ("Och i Rosengård —"), men upprepa inte samma övergångsord.
   - En avrundning på EN kort mening.

4. TON PER NYHET — det här är det viktigaste:
   - Lätta nyheter (kultur, sport, mat, kuriosa, väder, evenemang): var lekfull, använd gärna en ordvits.
   - Neutrala nyheter (politik, trafik, bygge, ekonomi): var rak och tydlig, med på sin höjd en torr kommentar.
   - Allvarliga nyheter (olyckor, brott, dödsfall, sjukdom, bränder, våld): var helt saklig och respektfull. INGA skämt, INGA ordvitsar, INGA ljudmarkörer. Sänk tempot i språket i stället.

5. RÖST OCH LJUDMARKÖRER — det här ger svepet sin karaktär:
   Varje nyhet ska ha sin EGEN tydliga röstkaraktär. Byt ton mellan varje inslag så att lyssnaren hör att ett nytt ämne börjat — sportnyheten ska låta som sport, kulturtipset som en entusiastisk vän, politiken som en luttrad kommentator.
   Inled VARJE stycke med en markör i hakparentes — vinjetten, varje nyhet och avrundningen — och återanvänd aldrig samma markör två gånger i samma manus. Manuset ska alltså ALLTID börja med en markör.
   Tillåtna markörer, och inga andra:
   [skrattar till], [dramatisk paus], [kort paus], [nyfiket], [torrt], [suckar], [lugnt], [glatt], [allvarligt], [viskar], [höjer rösten], [harklar sig], [eftertänksamt], [varmt], [förvånat], [entusiastiskt], [konspiratoriskt], [imponerat], [uppgivet], [triumferande], [medlidsamt], [pillemariskt], [sakligt], [andäktigt], [rappt]
   Låt också SPRÅKET byta karaktär med tonen, inte bara markören: korta stötiga meningar när det går undan, längre och lugnare när det är eftertänksamt. Markören [rappt] passar när du vill driva tempot extra.
   UNDANTAG: allvarliga nyheter (olyckor, brott, dödsfall, bränder, våld) får ALDRIG en markör och ska läsas neutralt och sakligt.

6. KONKRETION: En mening per nyhet betyder att du måste välja det som betyder något: siffran, platsen, vad som faktiskt ändras. Skriv "sju förskolor har lämnats", inte "förändringar inom förskolan". Undvik tomma fraser som "det händer mycket i stan" och "fortsätter sin resa".

7. TROHET: Hitta aldrig på detaljer, siffror, namn eller orsakssamband som inte står i underlaget. Om en nyhet saknar tidpunkt, påstå inte att den hände idag. Om du är osäker på en detalj, utelämna den hellre. Nämn källan när det känns naturligt ("enligt SVT").

8. BARA DET SOM HÄNT: Rapportera enbart sådant som redan har inträffat under det senaste dygnet. Tipsa ALDRIG om kommande konserter, evenemang, marknader eller föreställningar, och nämn aldrig ett framtida datum som något man kan gå på. Om ett underlag beskriver något som ännu inte hänt, hoppa över det helt.

9. FORMAT: Svara med ENBART manustexten. Ingen rubrik, inga punktlistor, inga markdown-tecken, inga regianvisningar utanför hakparenteserna, ingen inledande förklaring. Text som ska läsas rakt upp.`;

/** Must match rule 5 exactly — anything else is stripped before playback. */
export const ALLOWED_CUES = [
  'skrattar till', 'dramatisk paus', 'kort paus', 'nyfiket', 'torrt',
  'suckar', 'lugnt', 'glatt', 'allvarligt', 'viskar', 'höjer rösten',
  'harklar sig', 'eftertänksamt', 'varmt', 'förvånat',
  'entusiastiskt', 'konspiratoriskt', 'imponerat', 'uppgivet', 'triumferande',
  'medlidsamt', 'pillemariskt', 'sakligt', 'andäktigt', 'rappt',
];

const SWEDISH_DAYS = ['söndag', 'måndag', 'tisdag', 'onsdag', 'torsdag', 'fredag', 'lördag'];
const SWEDISH_MONTHS = ['januari', 'februari', 'mars', 'april', 'maj', 'juni',
  'juli', 'augusti', 'september', 'oktober', 'november', 'december'];

/** "för 3 timmar sedan" — what an anchor says, and it stops the model reading
 *  a raw timestamp aloud. */
export function relativeTime(published: Date | null, now: Date): string {
  if (!published) return 'publiceringstid okänd';
  const mins = Math.round((now.getTime() - published.getTime()) / 60_000);
  // A feed with a skewed clock can publish in the future; we do not know when
  // it really happened, so say so rather than call it breaking news.
  if (mins < 0) return 'publiceringstid okänd';
  if (mins < 1) return 'publicerad just nu';
  if (mins === 1) return 'publicerad för en minut sedan';
  if (mins < 60) return `publicerad för ${mins} minuter sedan`;
  // Floor, not round: rounding pushes 23h30m+ to 24 and mislabels a story that
  // passed the 24h cutoff as yesterday's.
  const hours = Math.floor(mins / 60);
  if (hours === 1) return 'publicerad för en timme sedan';
  if (hours < 24) return `publicerad för ${hours} timmar sedan`;
  return 'publicerad igår';
}

export function buildUserPrompt(stories: NewsItem[], now: Date = new Date()): string {
  const stamp = `${SWEDISH_DAYS[now.getDay()]} ${now.getDate()} ${SWEDISH_MONTHS[now.getMonth()]} ${now.getFullYear()}, klockan ${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;

  const blocks = stories.map((s, i) => {
    const lines = [
      `${i + 1}. [${s.sources.join(' och ')}, ${relativeTime(s.publishedAt, now)}]`,
      `   Rubrik: ${s.title}`,
      `   Sammanfattning: ${s.summary || 'saknas'}`,
    ];
    return lines.join('\n');
  });

  return `Datum och tid nu: ${stamp}.\n\nNyheter från det senaste dygnet:\n\n${blocks.join('\n\n')}`;
}

/** Outer safety valve: the transcript must survive URL encoding intact. */
export const MAX_ENCODED_TRANSCRIPT = 14 * 1024;
/**
 * The briefing is spoken in ONE /say call, so the whole script has to fit a
 * single URL segment and finish inside the shared 120s Sonos proxy timeout.
 * Measured on Kontor at ~0.1s of speech per character, so this 900-char ceiling
 * is ~85s of speech. The prompt itself targets 95-120 words (~700-800 chars),
 * which lands around 75s; this constant is the backstop, not the target.
 */
export const MAX_TRANSCRIPT_CHARS = 900;
const MIN_TRANSCRIPT_CHARS = 200;

function stripUnknownCues(text: string): string {
  return text.replace(/\[([^\]]*)\]/g, (whole, inner: string) =>
    ALLOWED_CUES.includes(inner.trim().toLowerCase()) ? whole : '',
  );
}

/** Truncates on a sentence boundary so the briefing never ends mid-word. */
function truncateAtSentence(text: string, max: number): string {
  if (text.length <= max) return text;
  const cut = text.slice(0, max);
  const lastStop = Math.max(cut.lastIndexOf('. '), cut.lastIndexOf('! '), cut.lastIndexOf('? '));
  if (lastStop > max * 0.5) return cut.slice(0, lastStop + 1).trim();
  // No sentence break to fall back on (one very long closing sentence). Cut at
  // the last word instead — a hard slice would be spoken mid-word.
  const lastSpace = cut.lastIndexOf(' ');
  const trimmed = (lastSpace > 0 ? cut.slice(0, lastSpace) : cut).trim();
  return /[.!?…]$/.test(trimmed) ? trimmed : `${trimmed}…`;
}

/**
 * Defence in depth against prompt drift: the model may still wrap the script in
 * markdown or invent a cue, and either would be read aloud verbatim.
 */
export function validateTranscript(raw: string): string {
  let text = raw.trim();

  text = text.replace(/^```[a-z]*\s*/i, '').replace(/```\s*$/, '');
  text = text.replace(/\*\*/g, '').replace(/^#+\s*/gm, '');
  text = stripUnknownCues(text);
  // The phrase becomes a single URL segment; newlines add nothing to prosody.
  text = text.replace(/\s+/g, ' ').trim();

  if (text.length < MIN_TRANSCRIPT_CHARS) {
    throw new Error(`Transcript too short (${text.length} chars) — refusing to play a truncated briefing`);
  }

  text = truncateAtSentence(text, MAX_TRANSCRIPT_CHARS);

  if (encodeURIComponent(text).length > MAX_ENCODED_TRANSCRIPT) {
    text = truncateAtSentence(text, Math.floor(MAX_TRANSCRIPT_CHARS / 2));
  }
  return text;
}

export async function generateScript(
  cfg: GeminiConfig,
  stories: NewsItem[],
  now: Date = new Date(),
): Promise<string> {
  const raw = await generateContent({
    cfg,
    systemInstruction: ANCHOR_SYSTEM_PROMPT,
    prompt: buildUserPrompt(stories, now),
  });
  return validateTranscript(raw);
}
