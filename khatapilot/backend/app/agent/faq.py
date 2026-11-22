"""Fixed answers to the questions people ask about KhataPilot itself: "is this safe?", "is this my real
bank?", "which AI are you?", "what happens if the AI is fooled?", "where is my data?" and so on.

They are matched before any model is called (app/agent/routing.py sends a match to the offline rules,
which answer from here), so the answer is the same every time, costs nothing, and works offline. A
question that matches nothing here goes to the model, whose system prompt carries the same facts.

Only plain questions match: a message with an amount or a phone number in it is a request about the
shop's money, never a question about KhataPilot, so it is never answered from here. A message in
Bangla script gets the Bangla answer; English and Banglish get the English one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.masking import find_phone_numbers
from app.core.money import normalize_digits

BANGLA = re.compile(r"[ঀ-৲৴-৿]")
# An amount: a number of 3+ digits, "5k", or a currency word next to a number ("tk 50", "50 taka", "৫০ টাকা").
AMOUNT = re.compile(r"\d{3,}|\d+\s*k\b|(?:৳|\btk\b|\btaka\b|টাকা)\s*\d|\d\s*(?:৳|tk\b|taka\b|টাকা)", re.I)


@dataclass(frozen=True)
class Faq:
    key: str
    pattern: re.Pattern[str]
    en: str
    bn: str


def _faq(key: str, pattern: str, en: str, bn: str) -> Faq:
    return Faq(key, re.compile(pattern, re.I), en, bn)


FAQS: tuple[Faq, ...] = (
    _faq("greeting",
         r"^\s*(?:hi+|hello+|hey|salam|assalamu?\s*alaikum|as-?salamu\s*alaikum|adab|nomoskar|good\s+(?:morning|"
         r"afternoon|evening))\W*$|^\s*(?:হ্যালো|হাই|আসসালামু\s*আলাইকুম|সালাম|আদাব|নমস্কার)\W*$",
         "Hello! I'm KhataPilot, your shop's bookkeeper. Tell me what you sold or bought, forward a bKash or Nagad "
         "SMS, ask \"will I run short of cash?\" or \"who owes me?\", or ask me to pay a supplier.",
         "হ্যালো! আমি খাতাপাইলট, আপনার দোকানের হিসাবরক্ষক। কী বিক্রি বা কেনা হলো বলুন, বিকাশ বা নগদের SMS "
         "ফরোয়ার্ড করুন, জিজ্ঞেস করুন \"টাকা কি কম পড়বে?\" বা \"কার কাছে বাকি আছে?\", অথবা সাপ্লায়ারকে টাকা "
         "পাঠাতে বলুন।"),
    _faq("thanks",
         r"^\s*(?:thanks?(?:\s+you)?(?:\s+(?:so\s+much|a\s+lot))?|thank\s*u|thx|ty|dhonnobad|dhonyobad|shukriya|"
         r"jazakallah\w*)\W*$|^\s*(?:ধন্যবাদ|শুকরিয়া)\W*$",
         "You're welcome! I'm here whenever you need the books, the cash outlook or a payment.",
         "আপনাকেও ধন্যবাদ! হিসাব, ক্যাশের অবস্থা বা কোনো পেমেন্ট লাগলে আমি আছি।"),
    _faq("identity",
         r"\b(?:who|what)\s+are\s+you\b|\bwhat\s+is\s+khata\s*pilot\b|\bare\s+you\s+(?:a\s+)?(?:human|real|person|"
         r"robot|bot|machine|ai)\b|\btumi\s+ke\b|\bapni\s+ke\b|তুমি\s*কে|আপনি\s*কে|খাতাপাইলট\s*কী|তুমি\s*কি\s*মানুষ",
         "I'm KhataPilot, an AI bookkeeper and finance assistant for small shops, not a person. I keep the books, "
         "warn you before cash runs short, collect baki, pay suppliers within your limits and help with loans. A "
         "fixed safety system (the guard) checks everything I propose before anything happens.",
         "আমি খাতাপাইলট, ছোট দোকানের জন্য একটি এআই হিসাবরক্ষক ও আর্থিক সহকারী, কোনো মানুষ নই। আমি হিসাব রাখি, "
         "ক্যাশ কম পড়ার আগে সতর্ক করি, বাকি আদায়ে সাহায্য করি, আপনার সীমার মধ্যে সাপ্লায়ারকে টাকা দিই এবং লোনে "
         "সাহায্য করি। আমি যা প্রস্তাব করি, তা হওয়ার আগে একটি নির্দিষ্ট নিরাপত্তা ব্যবস্থা (গার্ড) যাচাই করে।"),
    _faq("abilities",
         r"\bwhat\s+(?:can|do)\s+you\s+do\b|\bwhat\s+are\s+you\s+(?:able|for)\b|\bhow\s+(?:do\s+i|to|can\s+i)\s+use\s+"
         r"(?:you|this|khata\s*pilot|the\s+app)\b|^\s*help\W*$|\bki\s+ki\s+(?:korte|kora)\s+paro\b|"
         r"\bki\s+korte\s+paro\b|কী\s*কী\s*করতে\s*পারো|কি\s*কি\s*করতে\s*পারো|কী\s*করতে\s*পারো|কি\s*করতে\s*পারো|"
         r"কীভাবে\s*ব্যবহার",
         "I can record sales, purchases and baki from your words or a forwarded bKash/Nagad SMS, show today's totals "
         "and balances, warn you 14 days ahead if cash will run short, remind customers who owe you, pay trusted "
         "suppliers within your limits, check supplier invoices, make a monthly report you can save as a PDF (for "
         "you or a bank), and build a credit profile to match you with a loan. Try \"sold 3 bags of rice ৳1,500 "
         "cash\".",
         "আমি আপনার কথা বা ফরোয়ার্ড করা বিকাশ/নগদ SMS থেকে বিক্রি, কেনা ও বাকি লিখে রাখতে পারি, আজকের হিসাব ও "
         "ব্যালেন্স দেখাতে পারি, ১৪ দিন আগেই জানাতে পারি ক্যাশ কম পড়বে কি না, বাকি থাকা কাস্টমারদের মনে করিয়ে দিতে "
         "পারি, আপনার সীমার মধ্যে বিশ্বস্ত সাপ্লায়ারকে টাকা দিতে পারি, সাপ্লায়ারের ইনভয়েস যাচাই করতে পারি, মাসিক "
         "রিপোর্ট বানাতে পারি যা PDF হিসেবে রাখা যায় (আপনার বা ব্যাংকের জন্য), আর লোনের জন্য ক্রেডিট প্রোফাইল "
         "বানাতে পারি। লিখে দেখুন: \"৩ বস্তা চাল ১৫০০ টাকা নগদ বিক্রি\"।"),
    _faq("real_bank",
         r"\b(?:is\s+(?:this|it)|are\s+you)\s+(?:connected|linked)\s+to\s+(?:my\s+|a\s+|the\s+)?(?:real\s+)?(?:bank|bkash|"
         r"nagad|account)|\b(?:is\s+(?:this|it)|are\s+these)\s+(?:real|actual)\s+(?:money|bank|transactions?|accounts?)|"
         r"\breal\s+money\b|\bwhat\s+is\s+(?:the\s+)?sandbox\b|\bis\s+(?:this|it)\s+a\s+(?:demo|simulation|test)\b|"
         r"\basol\s+taka\b|আসল\s*টাকা|সত্যিকারের\s*ব্যাংক|আসল\s*ব্যাংক|স্যান্ডবক্স",
         "Not in this version. KhataPilot talks to a simulated bKash, Nagad, bank and lender (the Sandbox tab), and "
         "the shop's data is synthetic, so no real money moves. It connects the same way a real provider's API "
         "would: every payment carries the guard's signature, and the provider refuses anything the guard did not sign.",
         "এই সংস্করণে না। খাতাপাইলট একটি সিমুলেটেড বিকাশ, নগদ, ব্যাংক ও ঋণদাতার সাথে কাজ করে (স্যান্ডবক্স ট্যাব), আর "
         "দোকানের তথ্যগুলো কৃত্রিম, তাই কোনো আসল টাকা নড়ে না। আসল প্রোভাইডারের API যেভাবে যুক্ত হতো, এটিও সেভাবেই "
         "যুক্ত: প্রতিটি পেমেন্টে গার্ডের স্বাক্ষর থাকে, আর গার্ড স্বাক্ষর না করলে প্রোভাইডার তা ফিরিয়ে দেয়।"),
    _faq("model",
         r"\b(?:which|what)\s+(?:ai|a\.i\.|model|llm|language\s+model)\b|\bare\s+you\s+(?:chat\s*gpt|gpt|claude|gemini|"
         r"openai|deepseek|llama|an?\s+llm)\b|\bwho\s+powers\s+you\b|\bkon\s+(?:ai|model)\b|কোন\s*(?:এআই|AI|মডেল)",
         "When online I use Claude, a model made by Anthropic; simple requests and anything while the internet is "
         "down are answered by offline rules on this computer, at no cost. Whichever answers, the AI only proposes: "
         "a fixed rule engine, the guard, decides every action.",
         "অনলাইনে থাকলে আমি Anthropic-এর তৈরি Claude মডেল ব্যবহার করি; সহজ অনুরোধ আর ইন্টারনেট না থাকলে এই "
         "কম্পিউটারের অফলাইন নিয়ম উত্তর দেয়, কোনো খরচ ছাড়া। যেটাই উত্তর দিক, এআই শুধু প্রস্তাব করে: প্রতিটি কাজের "
         "সিদ্ধান্ত নেয় একটি নির্দিষ্ট নিয়মের ইঞ্জিন, গার্ড।"),
    _faq("ai_fooled",
         r"\bwhat\s+if\s+(?:the\s+|you\s+|someone\s+|a\s+)?(?:ai|model|you|hacker|scammer)?\s*(?:is|are|gets?|get|"
         r"were|was)?\s*(?:hacked|fooled|tricked|wrong|compromised|lies?|makes?\s+a\s+mistake)\b|\bprompt\s+injection\b|"
         r"\bcan\s+(?:you|the\s+ai|it)\s+be\s+(?:hacked|fooled|tricked)\b|\bcan\s+someone\s+(?:hack|trick|fool)\b|"
         r"হ্যাক|এআই\s*ভুল|প্রতারণা\s*করতে\s*পারে",
         "That is exactly what KhataPilot is built for. The AI may be fooled, so it has no authority: every action "
         "it proposes goes through the guard, which checks who really asked, your own words, the payee, your limits "
         "and a fraud score, and refuses anything that came from a forwarded SMS, an invoice or a stranger. The "
         "provider also refuses any payment without the guard's signature. In our tests 115 of 115 attacks were "
         "stopped with the model assumed fooled.",
         "খাতাপাইলট ঠিক এই জন্যই তৈরি। এআই ভুল করতে বা প্রতারিত হতে পারে, তাই তার কোনো ক্ষমতা নেই: সে যা প্রস্তাব "
         "করে তা গার্ড যাচাই করে, কে আসলে বলেছে, আপনার নিজের কথা, প্রাপক, আপনার সীমা আর প্রতারণার ঝুঁকি দেখে, আর "
         "ফরোয়ার্ড করা SMS, ইনভয়েস বা অচেনা কারও কথায় কিছু করে না। গার্ডের স্বাক্ষর ছাড়া প্রোভাইডারও কোনো পেমেন্ট "
         "নেয় না। আমাদের পরীক্ষায় এআই প্রতারিত ধরে নিয়েও ১১৫টির মধ্যে ১১৫টি আক্রমণ আটকানো গেছে।"),
    _faq("safe",
         r"\b(?:is|are)\s+(?:this|it|khata\s*pilot|you|my\s+(?:money|data|shop|account|information))\s+(?:safe|secure|"
         r"trustworthy)\b|\bcan\s+i\s+trust\s+(?:you|this|it|khata\s*pilot)\b|\bhow\s+(?:safe|secure)\s+is\b|"
         r"\bcan\s+(?:you|it|the\s+ai)\s+(?:steal|take|move)\s+my\s+money\b|\bnirapod\b|নিরাপদ|বিশ্বাস\s*করা\s*যায়|"
         r"ভরসা\s*করা\s*যায়",
         "Yes, by design. The AI can only propose; the guard decides. Payments go alone only to trusted suppliers "
         "within your limits and exactly as you asked; anything new or over a limit needs your PIN, and anything "
         "from a forwarded message or a stranger is refused. STOP freezes everything at once, and every decision is "
         "kept in a hash-chained audit log you can verify.",
         "হ্যাঁ, নকশা অনুযায়ীই। এআই শুধু প্রস্তাব করতে পারে; সিদ্ধান্ত নেয় গার্ড। শুধু বিশ্বস্ত সাপ্লায়ারকে, আপনার "
         "সীমার মধ্যে, আর ঠিক যেমন আপনি বলেছেন তেমনভাবেই পেমেন্ট নিজে থেকে যায়; নতুন কেউ বা সীমার বেশি হলে আপনার "
         "পিন লাগে, আর ফরোয়ার্ড করা মেসেজ বা অচেনা কারও কথায় কিছুই হয় না। STOP চাপলে সব একসাথে বন্ধ হয়, আর প্রতিটি "
         "সিদ্ধান্ত একটি হ্যাশ-চেইনড অডিট লগে থাকে যা আপনি যাচাই করতে পারেন।"),
    _faq("own_initiative",
         r"\b(?:can|will|do)\s+you\s+(?:pay|send|move|transfer)\s+(?:money\s+)?(?:without|on\s+your\s+own|by\s+yourself|"
         r"automatically)|\bwithout\s+my\s+(?:permission|approval|pin)\b|\bnijer\s+theke\s+(?:taka\s+)?(?:pathabe|dibe)\b|"
         r"অনুমতি\s*ছাড়া|নিজে\s*থেকে\s*টাকা",
         "Only within the rules you set: a payment you asked for, in your own words, to a trusted supplier and "
         "within your per-payment and daily limits. Anything else waits for your approval, and a new payee, a "
         "changed number or an amount over a limit always needs your PIN. You can change the limits in the "
         "Controls tab, with your PIN.",
         "শুধু আপনার ঠিক করা নিয়মের মধ্যে: আপনি নিজের কথায় যে পেমেন্ট চেয়েছেন, বিশ্বস্ত সাপ্লায়ারকে, আর প্রতি "
         "পেমেন্ট ও দৈনিক সীমার মধ্যে। বাকি সবকিছু আপনার অনুমোদনের অপেক্ষায় থাকে, আর নতুন প্রাপক, বদলানো নম্বর বা "
         "সীমার বেশি টাকায় সবসময় আপনার পিন লাগে। সীমা বদলাতে পারবেন কন্ট্রোলস ট্যাবে, পিন দিয়ে।"),
    _faq("privacy",
         r"\bwhere\s+(?:is|do\s+you\s+(?:keep|store))\s+my\s+(?:data|information|books)\b|\bwho\s+can\s+see\s+my\s+"
         r"(?:data|books|information)\b|\b(?:do|will)\s+you\s+(?:share|sell|send)\s+my\s+(?:data|information)\b|"
         r"\bprivacy\b|\bamar\s+(?:data|tottho)\s+(?:kothay|ke\s+dekhte\s+pay)\b|তথ্য\s*কোথায়|কে\s*দেখতে\s*পায়|"
         r"গোপনীয়তা",
         "Your books stay in KhataPilot, on the shop's own server. Nobody else sees them: a lender gets only a "
         "summary inside an application you approved, with no customer names or numbers. You can export everything "
         "(\"export my data\") or delete it (\"delete all my data\", with your PIN) any time.",
         "আপনার হিসাব খাতাপাইলটেই থাকে, দোকানের নিজের সার্ভারে। আর কেউ তা দেখে না: ঋণদাতা শুধু আপনার অনুমোদিত "
         "আবেদনের মধ্যে একটি সারাংশ পায়, কোনো কাস্টমারের নাম বা নম্বর ছাড়া। যেকোনো সময় সব তথ্য ডাউনলোড করতে পারেন "
         "(\"আমার সব তথ্য ডাউনলোড\") বা মুছে ফেলতে পারেন (\"আমার সব তথ্য মুছে দাও\", পিন দিয়ে)।"),
    _faq("how_delete",
         r"\bhow\s+(?:do|can)\s+i\s+(?:delete|erase|remove)\b|\bcan\s+i\s+(?:delete|erase|remove)\s+(?:my|all)\b|"
         r"কীভাবে\s*মুছ|কিভাবে\s*মুছ|মুছতে\s*পারি",
         "Say \"delete all my data\" here, or tap Delete all my data under Export in the Credit tab. It shows what "
         "will go, needs your PIN and cannot be undone, so export a copy first if you want one. Your profile, PIN, "
         "safety settings and the audit log stay.",
         "এখানে লিখুন \"আমার সব তথ্য মুছে দাও\", অথবা ক্রেডিট ট্যাবে এক্সপোর্টের নিচে Delete all my data চাপুন। কী "
         "মুছবে তা দেখাবে, পিন লাগবে, আর এটা ফেরানো যায় না, তাই চাইলে আগে একটি কপি এক্সপোর্ট করে নিন। আপনার "
         "প্রোফাইল, পিন, নিরাপত্তা সেটিংস আর অডিট লগ থেকে যায়।"),
    _faq("pin",
         r"\bwhy\s+(?:do\s+you\s+need|is\s+there|do\s+i\s+need)\s+(?:a\s+|my\s+|the\s+)?pin\b|\bwhen\s+do\s+(?:you|i)\s+need\s+"
         r"(?:a\s+|my\s+|the\s+)?pin\b|\bwhat\s+is\s+(?:the\s+|my\s+)?pin\s+for\b|\bpin\s+keno\b|পিন\s*কেন|কখন\s*পিন",
         "The PIN is your signature for anything risky: paying someone new, a changed number, an amount over your "
         "limits, changing limits or payees, resuming after STOP, deleting your data. Never type it in chat; it only "
         "goes into the PIN box, and three wrong tries lock money actions for 15 minutes.",
         "পিন হলো ঝুঁকির কাজে আপনার স্বাক্ষর: নতুন কাউকে টাকা দেওয়া, বদলানো নম্বর, সীমার বেশি টাকা, সীমা বা প্রাপক "
         "বদলানো, STOP-এর পর আবার চালু করা, তথ্য মুছে ফেলা। চ্যাটে কখনো পিন লিখবেন না; শুধু পিনের ঘরে দিন। তিনবার ভুল "
         "দিলে ১৫ মিনিটের জন্য টাকার কাজ বন্ধ থাকে।"),
    _faq("breaker",
         r"\bsafety\s+breaker\b|\bwhy\s+(?:is|did|was)\s+(?:everything|it|stop|khata\s*pilot)\s+(?:frozen|freeze|"
         r"froze|turned\s+on|on|stopped)\b|\bwhy\s+is\s+stop\s+on\b|সেফটি\s*ব্রেকার|কেন\s*বন্ধ\s*হয়ে",
         "The safety breaker pulls STOP by itself when someone keeps trying: 3 blocked attempts to move money or "
         "change settings within 15 minutes freeze every payment and outgoing message, and you get an alert. Check "
         "Alerts to see what was tried, then resume with your PIN.",
         "সেফটি ব্রেকার নিজে থেকেই STOP চালু করে যখন কেউ বারবার চেষ্টা করে: ১৫ মিনিটের মধ্যে টাকা পাঠানো বা সেটিংস "
         "বদলানোর ৩টি চেষ্টা আটকানো হলে সব পেমেন্ট ও বাইরে যাওয়া মেসেজ বন্ধ হয়ে যায়, আর আপনি সতর্কবার্তা পান। কী "
         "চেষ্টা হয়েছিল তা অ্যালার্টসে দেখুন, তারপর পিন দিয়ে আবার চালু করুন।"),
    _faq("stop",
         r"\bwhat\s+(?:is|does)\s+(?:the\s+)?(?:stop|kill\s*switch|stop\s+button)\b|\bhow\s+(?:do\s+i|to|can\s+i)\s+"
         r"(?:stop|freeze)\s+(?:everything|all|payments)\b|\bkill\s*switch\b|স্টপ\s*(?:বাটন|কী)",
         "STOP freezes everything at once: every payment and outgoing message is blocked, scheduled payments are "
         "paused (not deleted), and the provider freezes the shop's accounts too. Type \"stop\" or press the red "
         "button. Reading your books still works, and only your PIN turns it off.",
         "STOP চাপলে সব একসাথে বন্ধ হয়: প্রতিটি পেমেন্ট ও বাইরে যাওয়া মেসেজ আটকে যায়, নির্ধারিত পেমেন্ট থেমে থাকে "
         "(মুছে যায় না), আর প্রোভাইডারও দোকানের অ্যাকাউন্ট ফ্রিজ করে। \"stop\" লিখুন বা লাল বাটন চাপুন। হিসাব "
         "দেখা চালু থাকে, আর শুধু আপনার পিন দিয়ে তা বন্ধ করা যায়।"),
    _faq("invoice_check",
         r"\bwhat\s+is\s+(?:the\s+)?invoice\s+check\b|\bhow\s+do\s+you\s+check\s+(?:an\s+|the\s+)?invoices?\b|"
         r"\bfake\s+invoices?\b|ইনভয়েস\s*যাচাই|ভুয়া\s*ইনভয়েস",
         "Forward or photograph a supplier's invoice and I check it against your books before you pay: a payment "
         "number different from the one you have for that supplier, a \"new account\" note, the same invoice twice, "
         "a total far above what they usually charge, lines that don't add up, or hidden instructions for an AI. "
         "Anything wrong goes to Alerts; nothing is paid.",
         "সাপ্লায়ারের ইনভয়েস ফরোয়ার্ড করুন বা ছবি তুলুন, পেমেন্টের আগে আমি আপনার হিসাবের সাথে মিলিয়ে দেখি: ওই "
         "সাপ্লায়ারের যে নম্বর আপনার কাছে আছে তার চেয়ে আলাদা নম্বর, \"নতুন অ্যাকাউন্ট\" লেখা, একই ইনভয়েস দুবার, "
         "সাধারণের চেয়ে অনেক বেশি টাকা, যোগফল না মেলা, বা এআই-এর জন্য লুকানো নির্দেশ। কিছু ভুল থাকলে অ্যালার্টসে "
         "যায়; কোনো টাকা দেওয়া হয় না।"),
    _faq("tiers",
         r"\bwhat\s+(?:are|is)\s+(?:the\s+|a\s+)?(?:tiers?|permission\s+(?:tiers?|levels?))\b|\bpermission\s+levels?\b|"
         r"\bhow\s+do\s+you\s+decide\b|টিয়ার|অনুমতির\s*স্তর",
         "Every action gets a tier. Tier 0: reading and your own books, done at once. Tier 1: messages and loan "
         "applications, which wait for your Approve. Tier 2: a payment to a trusted supplier within your limits, "
         "done and reported. Tier 3: anything new, changed or over a limit, which needs your confirmation and PIN. "
         "Attacks are refused outright.",
         "প্রতিটি কাজের একটি স্তর থাকে। স্তর ০: দেখা আর আপনার নিজের হিসাব, সাথে সাথে হয়। স্তর ১: মেসেজ ও লোনের আবেদন, "
         "আপনার Approve-এর অপেক্ষায় থাকে। স্তর ২: সীমার মধ্যে বিশ্বস্ত সাপ্লায়ারকে পেমেন্ট, হয়ে যায় ও জানানো হয়। "
         "স্তর ৩: নতুন, বদলানো বা সীমার বেশি কিছু, যাতে আপনার নিশ্চিতকরণ ও পিন লাগে। আক্রমণ সরাসরি ফিরিয়ে দেওয়া হয়।"),
    _faq("audit",
         r"\bwhat\s+is\s+(?:the\s+)?audit\s+log\b|\bcan\s+(?:you|anyone|someone)\s+(?:change|edit|delete|fake)\s+"
         r"(?:the\s+)?(?:records|log|audit|history)\b|\bhash[\s-]*chain\w*\b|অডিট\s*লগ",
         "The audit log records every decision: who asked, each check, the result and why, with phone numbers "
         "masked. It is append-only and hash-chained, so changing or deleting any past entry breaks the chain; "
         "\"verify chain\" in the Audit tab checks it.",
         "অডিট লগে প্রতিটি সিদ্ধান্ত থাকে: কে চেয়েছিল, প্রতিটি যাচাই, ফল আর কারণ, ফোন নম্বর ঢাকা অবস্থায়। এটিতে শুধু "
         "যোগ করা যায় আর হ্যাশ-চেইনড, তাই আগের কোনো লেখা বদলালে বা মুছলে চেইন ভেঙে যায়; অডিট ট্যাবের \"verify "
         "chain\" তা যাচাই করে।"),
    _faq("offline",
         r"\b(?:do|can|will)\s+you\s+work\s+(?:offline|without\s+(?:the\s+)?internet)\b|\bwithout\s+(?:the\s+)?internet\b|"
         r"\bwhat\s+if\s+(?:the\s+)?internet\b|\binternet\s+chara\b|ইন্টারনেট\s*ছাড়া|অফলাইনে",
         "Yes. Plain requests (sales, totals, balances, who owes you, the cash forecast) and these questions are "
         "answered by offline rules on this computer, and voice works offline too. Requests that need understanding "
         "wait for the AI; nothing unsafe is ever done because it is offline.",
         "হ্যাঁ। সাধারণ অনুরোধ (বিক্রি, আজকের হিসাব, ব্যালেন্স, কার কাছে বাকি, ক্যাশের পূর্বাভাস) আর এই প্রশ্নগুলোর "
         "উত্তর এই কম্পিউটারের অফলাইন নিয়ম দেয়, আর ভয়েসও অফলাইনে কাজ করে। যেসব অনুরোধ বুঝতে হয় সেগুলো এআই-এর "
         "অপেক্ষায় থাকে; অফলাইন বলে কখনো ঝুঁকির কিছু করা হয় না।"),
    _faq("languages",
         r"\bwhat\s+languages?\b|\b(?:do|can)\s+you\s+(?:speak|understand|talk\s+in)\s+(?:bangla|bengali|banglish)\b|"
         r"\bbangla(?:y)?\s+(?:bolte|bujhte)\s+paro\b|বাংলা\s*(?:বলতে|বুঝতে)\s*পারো|বাংলায়\s*কথা",
         "English, Banglish (\"Jamal ke 450 taka baki dilam\") and Bangla. You can also speak: the microphone works "
         "offline on this computer.",
         "ইংরেজি, বাংলিশ (\"Jamal ke 450 taka baki dilam\") আর বাংলা। মুখে বলেও দিতে পারেন: মাইক্রোফোন এই কম্পিউটারে "
         "অফলাইনেও কাজ করে।"),
    _faq("credit",
         r"\bhow\s+(?:is|do\s+you\s+(?:calculate|work\s+out|decide))\s+(?:my\s+|the\s+)?(?:credit\s+(?:score|band|"
         r"profile|rating)|band)\b|\bwhat\s+is\s+(?:a\s+|my\s+)?credit\s+(?:band|score)\s+based\s+on\b|"
         r"\bwhat\s+is\s+a\s+credit\s+band\b|ক্রেডিট\s*স্কোর\s*কীভাবে|ক্রেডিট\s*ব্যান্ড\s*কী",
         "With your consent, the credit band (A to E) comes from how the shop runs: steady sales, baki collected on "
         "time, suppliers paid on time, cash kept on hand and how long the books go back. Your shop type, area and "
         "district are never used. Each band comes with its main reasons in plain words, in the Credit tab.",
         "আপনার সম্মতিতে, ক্রেডিট ব্যান্ড (A থেকে E) আসে দোকান কীভাবে চলে তা থেকে: নিয়মিত বিক্রি, সময়মতো বাকি "
         "আদায়, সময়মতো সাপ্লায়ারকে টাকা দেওয়া, হাতে রাখা ক্যাশ আর হিসাব কতদিনের। আপনার দোকানের ধরন, এলাকা বা জেলা "
         "কখনো ব্যবহার হয় না। প্রতিটি ব্যান্ডের প্রধান কারণগুলো সহজ ভাষায় ক্রেডিট ট্যাবে থাকে।"),
    _faq("advice_limits",
         r"\bare\s+you\s+a\s+(?:licensed\s+)?(?:financial|investment)\s+(?:advisor|adviser|planner)\b|\b(?:should|can)\s+i\s+"
         r"(?:invest|buy)\s+(?:in\s+)?(?:stocks?|shares?|crypto|bitcoin|gold|land)\b|\binvestment\s+advice\b|"
         r"\bshare\s*bazar\b|শেয়ার\s*বাজার|বিনিয়োগ\s*পরামর্শ",
         "I'm not a licensed financial adviser, and I don't give advice on stocks, crypto or other investments. I can "
         "help with the shop's own money: cash, baki, savings set aside from profit (like bKash DPS or "
         "Sanchayapatra), stock and loans, using the numbers in your books.",
         "আমি লাইসেন্সপ্রাপ্ত আর্থিক উপদেষ্টা নই, আর শেয়ার, ক্রিপ্টো বা অন্য বিনিয়োগে পরামর্শ দিই না। আমি দোকানের "
         "নিজের টাকায় সাহায্য করতে পারি: ক্যাশ, বাকি, লাভ থেকে সঞ্চয় (যেমন বিকাশ ডিপিএস বা সঞ্চয়পত্র), মাল আর লোন, "
         "আপনার হিসাবের সংখ্যা দিয়ে।"),
    _faq("khata",
         r"\bwhat\s+(?:is|does)\s+(?:a\s+)?(?:khata|baki)(?:\s+mean)?\b|\bwhat\s+does\s+khata\s*pilot\s+mean\b|"
         r"বাকি\s*মানে|খাতা\s*মানে",
         "A khata is the notebook a Bangladeshi shop keeps its accounts in, and baki is what customers take on "
         "credit and pay later. KhataPilot keeps that khata for you, digitally, and turns it into a cash forecast "
         "and a credit profile.",
         "খাতা হলো সেই খাতা যাতে দোকানের হিসাব লেখা হয়, আর বাকি হলো কাস্টমার এখন নিয়ে পরে যে টাকা দেবে। খাতাপাইলট "
         "আপনার হয়ে সেই খাতা ডিজিটালভাবে রাখে, আর তা থেকে ক্যাশের পূর্বাভাস ও ক্রেডিট প্রোফাইল বানায়।"),
    _faq("makers",
         r"\bwho\s+(?:made|built|created|developed|owns)\s+(?:you|this|khata\s*pilot|the\s+app)\b|\bwho\s+is\s+behind\b|"
         r"তোমাকে\s*কে\s*বানিয়েছে|কে\s*তৈরি\s*করেছে",
         "KhataPilot was built by a student team from Bangladesh for the 2026 Shenzhen International FinTech "
         "Competition (Topic E, SME Finance Copilot).",
         "খাতাপাইলট বানিয়েছে বাংলাদেশের একটি শিক্ষার্থী দল, ২০২৬ শেনজেন আন্তর্জাতিক ফিনটেক প্রতিযোগিতার জন্য (বিষয় E, "
         "এসএমই ফাইন্যান্স কোপাইলট)।"),
)


def match(text: str) -> Faq | None:
    """The FAQ this plain question is, or None. Requests that carry an amount or a number never match."""
    t = normalize_digits(text or "").strip()
    if not t or len(t) > 160 or AMOUNT.search(t) or find_phone_numbers(t):
        return None
    for faq in FAQS:
        if faq.pattern.search(t):
            return faq
    return None


def answer(text: str) -> str | None:
    faq = match(text)
    if faq is None:
        return None
    return faq.bn if BANGLA.search(text) else faq.en
