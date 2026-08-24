You are a Principal Data engineer and Integration specialist. The code you generate will be Production grade and with all the best practises and no hard coding. 

1)The B Documents have two or more data sources to look for or pull. GST portal, GST APIs and also External Web or our Inafin ERP Store.
2) I want you to build Adapters to pull data from GST portal, GST APis, or Extternal web or our infain store. Let follow connector/adapter approach where tomorrow i can add new one easily
3) If you refer the inafin_recon_doc4_sec2_sourceDocRegister_v2.docx(1).pdf for each time document its relevant details For example B1.01 description says "GSTR-1 — all periods, all 
tables: B2B (T4), B2CL (T5), 
EXP (T6A/6B), B2CS (T7), 
EXEMP (T8), CDNR (T9), 
CDNUR (T10), AT (T11), 
amendments (T9A/9B/9C)" and GSTIN API to full the data and Where does it be largely refered or used "A1,A6,A7,B6" and both mode Forensic and regular. and some remarks.  The description is hint what type of data we are pulling from GSTIN API or Portal where it would be one single document or it might be multiple types. I want you to USE YOUR GST KNOWLEDGE TO Write the adapter to download them .  Also there should be mapping table in DB where we will store B1.01 will be used by A1,A6,A7,B6 and its applicable to both forensic and regular.
4) I have provided the Sample data for all of these in B-documents folder u can go thorough and understand them. There is a Readme.md 
5) As we dont have real credentails ofr GST portal, GST API or external portal, Plan is we will build HTTP Connectors/adapter for it. however we will have a flag in Config to refer from folder from bronze so, the processing pipeline checks this and instead of calling HTTP connetors/adapters it refers to the folder. You pleasse structure the folder based on tenat/type of document/subsection or type(B1.01) etc
6) Ask any questuons if u are not clear.
7) As usual while testing if it fails more than 2 iteration please stop and let me know rather going in ednless loop
8) keep the code as much as modular and follow SRP, Interfaces abstraction and Docstrings and comments