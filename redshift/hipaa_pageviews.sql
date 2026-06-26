CASE
  WHEN page_path LIKE '/hipaa/for-professionals/%' THEN 'HIPAA FAQs'
  ELSE NULL
END AS section

WITH section_pages AS (

SELECT
    user_pseudo_id,
    session_id,
    event_timestamp,
    page_path,
    section,

    ROW_NUMBER() OVER (
        PARTITION BY user_pseudo_id, session_id
        ORDER BY event_timestamp
    ) AS page_order

FROM analytics.pageviews

WHERE section = 'HIPAA FAQs'

)

SELECT
    *,
    LAG(page_path) OVER (
        PARTITION BY user_pseudo_id, session_id
        ORDER BY page_order
    ) AS previous_page

FROM section_pages;