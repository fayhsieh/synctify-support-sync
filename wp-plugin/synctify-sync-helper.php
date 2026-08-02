<?php
/**
 * Plugin Name: Synctify Sync Helper
 * Description: Notion → n8n → WordPress 自動上稿流程的輔助端點：開啟 Arconix FAQ REST、寫入 Elementor data、讀寫 TranslatePress 字典表、寫入 AIOSEO meta。
 * Version: 0.1.4
 * Author: Synctify Marketing (Fay)
 *
 * 安裝：外掛 → 上傳外掛（打包成 zip），或直接放入 wp-content/mu-plugins/
 * 認證：所有自訂端點皆要求 Application Password（Basic Auth）且使用者具 edit_posts 權限
 */

if ( ! defined( 'ABSPATH' ) ) exit;

/* ---------------------------------------------------------------
 * 1. Arconix FAQ post type 開啟 REST
 * ------------------------------------------------------------- */
add_filter( 'register_post_type_args', function ( $args, $post_type ) {
	if ( 'faq' === $post_type ) {
		$args['show_in_rest'] = true;
		$args['rest_base']    = 'faq';
	}
	return $args;
}, 10, 2 );

add_filter( 'register_taxonomy_args', function ( $args, $taxonomy ) {
	// Arconix FAQ 的分組 taxonomy（實際名稱若不同，改這裡）
	if ( 'group' === $taxonomy ) {
		$args['show_in_rest'] = true;
		$args['rest_base']    = 'faq-group';
	}
	return $args;
}, 10, 2 );

/* ---------------------------------------------------------------
 * 2. 自訂 REST 端點
 * ------------------------------------------------------------- */
add_action( 'rest_api_init', function () {

	$permission = function () {
		return current_user_can( 'edit_posts' );
	};

	/* 2a. 寫入 Elementor data（protected meta，標準 REST 不開放）
	 * POST /wp-json/synctify/v1/elementor/<post_id>
	 * body: { "elementor_data": [ ...Elementor JSON 陣列... ] }
	 */
	register_rest_route( 'synctify/v1', '/elementor/(?P<id>\d+)', array(
		'methods'             => 'POST',
		'permission_callback' => $permission,
		'callback'            => function ( WP_REST_Request $req ) {
			$post_id = (int) $req['id'];
			if ( ! get_post( $post_id ) ) {
				return new WP_Error( 'not_found', 'Post not found', array( 'status' => 404 ) );
			}
			$data = $req->get_json_params();
			if ( empty( $data['elementor_data'] ) || ! is_array( $data['elementor_data'] ) ) {
				return new WP_Error( 'bad_request', 'elementor_data (array) is required', array( 'status' => 400 ) );
			}

			// 覆蓋前備份（保留最近 3 份）
			$backups   = get_post_meta( $post_id, '_synctify_elementor_backups', true ) ?: array();
			$current   = get_post_meta( $post_id, '_elementor_data', true );
			if ( $current ) {
				array_unshift( $backups, array( 'time' => current_time( 'mysql' ), 'data' => $current ) );
				$backups = array_slice( $backups, 0, 3 );
				update_post_meta( $post_id, '_synctify_elementor_backups', $backups );
			}

			// _elementor_data 以 JSON 字串儲存；wp_slash 防止反斜線被剝除
			update_post_meta( $post_id, '_elementor_data', wp_slash( wp_json_encode( $data['elementor_data'] ) ) );
			update_post_meta( $post_id, '_elementor_edit_mode', 'builder' );
			update_post_meta( $post_id, '_elementor_template_type', 'wp-post' );
			if ( defined( 'ELEMENTOR_VERSION' ) ) {
				update_post_meta( $post_id, '_elementor_version', ELEMENTOR_VERSION );
			}

			// 清除該文章的 Elementor CSS 快取，強制重新生成
			if ( class_exists( '\Elementor\Plugin' ) ) {
				\Elementor\Plugin::$instance->files_manager->clear_cache();
			}

			return array( 'ok' => true, 'post_id' => $post_id, 'backups_kept' => count( $backups ) );
		},
	) );

	/* 2b. 還原 Elementor data 備份
	 * POST /wp-json/synctify/v1/elementor/<post_id>/restore  body: { "index": 0 }
	 */
	register_rest_route( 'synctify/v1', '/elementor/(?P<id>\d+)/restore', array(
		'methods'             => 'POST',
		'permission_callback' => $permission,
		'callback'            => function ( WP_REST_Request $req ) {
			$post_id = (int) $req['id'];
			$index   = (int) ( $req->get_json_params()['index'] ?? 0 );
			$backups = get_post_meta( $post_id, '_synctify_elementor_backups', true );
			if ( empty( $backups[ $index ] ) ) {
				return new WP_Error( 'not_found', 'Backup not found', array( 'status' => 404 ) );
			}
			update_post_meta( $post_id, '_elementor_data', wp_slash( $backups[ $index ]['data'] ) );
			if ( class_exists( '\Elementor\Plugin' ) ) {
				\Elementor\Plugin::$instance->files_manager->clear_cache();
			}
			return array( 'ok' => true, 'restored_from' => $backups[ $index ]['time'] );
		},
	) );

	/* 2b-2. 由網址匯入圖片到媒體庫（sideload）
	 * POST /wp-json/synctify/v1/media/sideload
	 * body: {
	 *   "images": [ { "url": "...", "alt": "...", "filename": "選填.png" }, ... ],
	 *   "post_id": 123   // 選填，附加到該文章
	 * }
	 *
	 * 為什麼放在 WP 端而不是 n8n：Notion 的 S3 網址是預簽章、一小時後失效，
	 * 且 [caption] shortcode 需要 wp-image-{id} 與 -1024x576 縮圖網址——
	 * 這些只有上傳完成後 WP 才知道，由端點一併回傳可省去 n8n 端的二進位處理與迴圈。
	 *
	 * 回傳每張圖的 media id、原圖網址、large 尺寸網址與實際寬高，供呼叫端回填版面。
	 * 同名檔案已存在時直接重用，不重複上傳（可安全重跑）。
	 */
	register_rest_route( 'synctify/v1', '/media/sideload', array(
		'methods'             => 'POST',
		'permission_callback' => $permission,
		'callback'            => function ( WP_REST_Request $req ) {
			require_once ABSPATH . 'wp-admin/includes/file.php';
			require_once ABSPATH . 'wp-admin/includes/media.php';
			require_once ABSPATH . 'wp-admin/includes/image.php';

			$params  = $req->get_json_params();
			$images  = $params['images'] ?? array();
			$post_id = isset( $params['post_id'] ) ? (int) $params['post_id'] : 0;
			if ( ! is_array( $images ) ) {
				return new WP_Error( 'bad_request', 'images must be an array', array( 'status' => 400 ) );
			}
			// 空陣列視為合法（文章沒有待上傳圖片時），回傳空結果即可，
			// 讓呼叫端不必為了「這篇沒圖」而多開一條分支。
			if ( empty( $images ) ) {
				return array( 'ok' => true, 'images' => array() );
			}

			$out = array();
			foreach ( $images as $img ) {
				$url = isset( $img['url'] ) ? esc_url_raw( $img['url'] ) : '';
				if ( ! $url ) {
					$out[] = array( 'ok' => false, 'error' => 'missing url' );
					continue;
				}
				$alt = isset( $img['alt'] ) ? sanitize_text_field( $img['alt'] ) : '';
				// Notion 的圖片只有「圖說」一個欄位，預設同時作為 alt 與 caption；
				// 呼叫端可另外指定 caption 以區隔兩者。
				$caption = isset( $img['caption'] ) ? sanitize_text_field( $img['caption'] ) : $alt;

				// 檔名：優先用呼叫端指定的，否則取網址路徑（去掉查詢字串——S3 預簽章參數很長）
				$filename = isset( $img['filename'] ) && $img['filename']
					? sanitize_file_name( $img['filename'] )
					: sanitize_file_name( basename( parse_url( $url, PHP_URL_PATH ) ) );
				if ( ! $filename ) {
					$filename = 'synctify-image.png';
				}

				// 已有同名附件 → 重用，避免重跑時產生重複媒體
				$existing = get_posts( array(
					'post_type'      => 'attachment',
					'post_status'    => 'inherit',
					'posts_per_page' => 1,
					'fields'         => 'ids',
					'meta_query'     => array( array(
						'key'     => '_synctify_source_filename',
						'value'   => $filename,
					) ),
				) );
				if ( ! empty( $existing ) ) {
					// 重用既有附件時也同步更新文字欄位，讓 Notion 端的修正能傳遞過來
					// （否則第一次上傳寫錯的內容永遠改不掉，除非人工刪除媒體重跑）
					synctify_apply_media_text( (int) $existing[0], $alt, $caption );
					$out[] = synctify_media_payload( (int) $existing[0], $url, true );
					continue;
				}

				$tmp = download_url( $url, 60 );
				if ( is_wp_error( $tmp ) ) {
					$out[] = array( 'ok' => false, 'source_url' => $url,
					                'error' => 'download failed: ' . $tmp->get_error_message() );
					continue;
				}
				$file_array = array( 'name' => $filename, 'tmp_name' => $tmp );
				// 第 3 參數 $desc → post_title；第 4 參數可直接帶入 post_excerpt（＝媒體庫的 Caption）
				$attach_id  = media_handle_sideload(
					$file_array, $post_id, $alt,
					array( 'post_excerpt' => $caption )
				);
				if ( is_wp_error( $attach_id ) ) {
					@unlink( $tmp );
					$out[] = array( 'ok' => false, 'source_url' => $url,
					                'error' => 'sideload failed: ' . $attach_id->get_error_message() );
					continue;
				}
				synctify_apply_media_text( (int) $attach_id, $alt, $caption );
				update_post_meta( $attach_id, '_synctify_source_filename', $filename );
				$out[] = synctify_media_payload( (int) $attach_id, $url, false );
			}

			return array( 'ok' => true, 'images' => $out );
		},
	) );

	/* 2b-3. 【實驗性】把版面寫成 Elementor 草稿（不動前台）
	 * POST /wp-json/synctify/v1/elementor/<post_id>/draft
	 * body: { "elementor_data": [ ... ] }
	 *
	 * 目的：驗證「已發佈文章可存 Elementor 草稿、前台不受影響」能否從程式端達成。
	 * 若可行，更新既有文章就不需要「影子草稿」那套（另建 [更新預覽] 文章再搬回）。
	 *
	 * 做法：取得該文章的 Elementor Document，透過 get_autosave(0, true) 取得／建立
	 * autosave 版本，只對 autosave 寫入版面。主文章的 _elementor_data 完全不動。
	 *
	 * 回傳 autosave_id 與寫入前後的主文章 _elementor_data 雜湊，
	 * 方便呼叫端確認「前台內容真的沒被改到」。
	 */
	register_rest_route( 'synctify/v1', '/elementor/(?P<id>\d+)/draft', array(
		'methods'             => 'POST',
		'permission_callback' => $permission,
		'callback'            => function ( WP_REST_Request $req ) {
			$post_id = (int) $req['id'];
			$post    = get_post( $post_id );
			if ( ! $post ) {
				return new WP_Error( 'not_found', 'Post not found', array( 'status' => 404 ) );
			}
			$data = $req->get_json_params();
			if ( empty( $data['elementor_data'] ) || ! is_array( $data['elementor_data'] ) ) {
				return new WP_Error( 'bad_request', 'elementor_data (array) is required', array( 'status' => 400 ) );
			}
			if ( ! class_exists( '\Elementor\Plugin' ) ) {
				return new WP_Error( 'no_elementor', 'Elementor not active', array( 'status' => 501 ) );
			}

			// 寫入前先記錄主文章版面的指紋，之後比對確認前台未被更動
			$before = md5( (string) get_post_meta( $post_id, '_elementor_data', true ) );

			$documents = \Elementor\Plugin::$instance->documents;
			$document  = $documents->get( $post_id );
			if ( ! $document ) {
				return new WP_Error( 'no_document', 'Elementor document not found for this post',
				                     array( 'status' => 500 ) );
			}
			if ( ! method_exists( $document, 'get_autosave' ) ) {
				return new WP_Error( 'unsupported',
					'This Elementor version has no Document::get_autosave()',
					array( 'status' => 501 ) );
			}

			$autosave = $document->get_autosave( 0, true );   // 第二個參數 true = 沒有就建立
			if ( ! $autosave ) {
				return new WP_Error( 'autosave_failed', 'Could not create autosave document',
				                     array( 'status' => 500 ) );
			}

			$saved = $autosave->save( array(
				'elements' => $data['elementor_data'],
			) );

			$after = md5( (string) get_post_meta( $post_id, '_elementor_data', true ) );

			return array(
				'ok'                 => (bool) $saved,
				'post_id'            => $post_id,
				'post_status'        => $post->post_status,
				'autosave_id'        => method_exists( $autosave, 'get_main_id' )
				                        ? $autosave->get_main_id() : null,
				// 兩者相同代表主文章版面沒被動到——前台不受影響
				'live_data_unchanged' => ( $before === $after ),
				'note'               => '實驗性端點：請在 Elementor 開啟此文章確認是否出現'
				                        . '「有較新的草稿版本」提示，並確認前台仍為舊內容。',
			);
		},
	) );

	/* 2c. TranslatePress 字典表查詢
	 * POST /wp-json/synctify/v1/tp/lookup
	 * body: { "language": "zh_CN", "strings": [ "原文1", "原文2", ... ] }
	 * 回傳每筆的 translated 與 status（0=未翻譯 1=機翻 2=人工）；不在表中的回傳 status=-1
	 */
	register_rest_route( 'synctify/v1', '/tp/lookup', array(
		'methods'             => 'POST',
		'permission_callback' => $permission,
		'callback'            => function ( WP_REST_Request $req ) {
			$table = synctify_tp_table( $req->get_json_params()['language'] ?? '' );
			if ( is_wp_error( $table ) ) return $table;
			$strings = $req->get_json_params()['strings'] ?? array();
			if ( ! is_array( $strings ) || empty( $strings ) ) {
				return new WP_Error( 'bad_request', 'strings (array) is required', array( 'status' => 400 ) );
			}
			global $wpdb;
			$out = array();
			foreach ( $strings as $s ) {
				$row = $wpdb->get_row( $wpdb->prepare(
					"SELECT id, translated, status FROM {$table} WHERE original = %s LIMIT 1", $s
				) );
				$out[] = array(
					'original'   => $s,
					'id'         => $row ? (int) $row->id : null,
					'translated' => $row ? $row->translated : null,
					'status'     => $row ? (int) $row->status : -1,
				);
			}
			return $out;
		},
	) );

	/* 2d. TranslatePress 字典表寫入譯文
	 * POST /wp-json/synctify/v1/tp/update
	 * body: { "language": "zh_CN", "items": [ { "id": 123, "translated": "譯文" }, ... ] }
	 * 一律寫入 status=1（機器翻譯）；已是 status=2（人工翻譯）的字串跳過不覆蓋
	 */
	register_rest_route( 'synctify/v1', '/tp/update', array(
		'methods'             => 'POST',
		'permission_callback' => $permission,
		'callback'            => function ( WP_REST_Request $req ) {
			$table = synctify_tp_table( $req->get_json_params()['language'] ?? '' );
			if ( is_wp_error( $table ) ) return $table;
			$items = $req->get_json_params()['items'] ?? array();
			global $wpdb;
			$updated = 0; $skipped = 0; $not_found = 0; $failed = 0;
			foreach ( $items as $item ) {
				if ( empty( $item['id'] ) || ! isset( $item['translated'] ) ) continue;
				// 注意：get_var 對「不存在的列」與「status=0（未翻譯）」都會讓 (int) 轉成 0，
				// 必須先用 null 判斷該列是否存在，否則過期的 id 會被當成可寫入，
				// 實際一列都沒寫到卻回報成功（呼叫端會誤以為譯文已寫入）。
				$row_status = $wpdb->get_var( $wpdb->prepare(
					"SELECT status FROM {$table} WHERE id = %d", (int) $item['id']
				) );
				if ( null === $row_status ) { $not_found++; continue; } // 該列不存在（id 可能已失效）
				if ( 2 === (int) $row_status ) { $skipped++; continue; } // 人工翻譯不覆蓋
				$affected = $wpdb->update(
					$table,
					array( 'translated' => $item['translated'], 'status' => 1 ),
					array( 'id' => (int) $item['id'] ),
					array( '%s', '%d' ), array( '%d' )
				);
				// 回傳 false 為 DB 錯誤；0 代表該列值未變動（已確認列存在，視為成功）
				if ( false === $affected ) { $failed++; continue; }
				$updated++;
			}
			return array(
				'ok'            => true,
				'updated'       => $updated,
				'skipped_human' => $skipped,
				'not_found'     => $not_found,
				'failed'        => $failed,
			);
		},
	) );

	/* 2e. 寫入 AIOSEO meta title / description
	 * POST /wp-json/synctify/v1/seo/<post_id>
	 * body: { "title": "...", "description": "..." }
	 */
	register_rest_route( 'synctify/v1', '/seo/(?P<id>\d+)', array(
		'methods'             => 'POST',
		'permission_callback' => $permission,
		'callback'            => function ( WP_REST_Request $req ) {
			$post_id = (int) $req['id'];
			if ( ! get_post( $post_id ) ) {
				return new WP_Error( 'not_found', 'Post not found', array( 'status' => 404 ) );
			}
			$p = $req->get_json_params();
			if ( ! function_exists( 'aioseo' ) ) {
				return new WP_Error( 'no_aioseo', 'AIOSEO not active', array( 'status' => 501 ) );
			}
			$aioseo_post = \AIOSEO\Plugin\Common\Models\Post::getPost( $post_id );
			$aioseo_post->post_id = $post_id;
			if ( isset( $p['title'] ) )       $aioseo_post->title       = sanitize_text_field( $p['title'] );
			if ( isset( $p['description'] ) ) $aioseo_post->description = sanitize_text_field( $p['description'] );
			$aioseo_post->save();
			return array( 'ok' => true, 'post_id' => $post_id );
		},
	) );
} );

/* ---------------------------------------------------------------
 * 媒體回傳格式：一次給齊呼叫端回填版面所需的資訊
 *   full_url   原圖（[caption] 的 <a href>，即 Link To = Media File）
 *   large_url  large 尺寸（<img src>，站方統一 1024 寬）
 *   width/height 實際尺寸（非 16:9 的圖高度不是 576，需用實際值）
 * ------------------------------------------------------------- */
/**
 * 寫入附件的文字欄位。三者在 WP 是不同的儲存位置，很容易寫錯：
 *   Alt text → post meta `_wp_attachment_image_alt`
 *   Caption  → `post_excerpt`（不是 post_content，後者是 Description）
 *   Title    → `post_title`（由 media_handle_sideload 的 $desc 帶入）
 */
function synctify_apply_media_text( $attach_id, $alt, $caption ) {
	if ( '' !== $alt ) {
		update_post_meta( $attach_id, '_wp_attachment_image_alt', $alt );
	}
	if ( '' !== $caption ) {
		wp_update_post( array( 'ID' => $attach_id, 'post_excerpt' => $caption ) );
	}
}

function synctify_media_payload( $attach_id, $source_url, $reused ) {
	$full  = wp_get_attachment_image_src( $attach_id, 'full' );
	$large = wp_get_attachment_image_src( $attach_id, 'large' );
	return array(
		'ok'         => true,
		'source_url' => $source_url,
		'reused'     => (bool) $reused,
		'id'         => $attach_id,
		'full_url'   => $full ? $full[0] : wp_get_attachment_url( $attach_id ),
		'large_url'  => $large ? $large[0] : wp_get_attachment_url( $attach_id ),
		'width'      => $large ? (int) $large[1] : null,
		'height'     => $large ? (int) $large[2] : null,
		'alt'        => get_post_meta( $attach_id, '_wp_attachment_image_alt', true ),
		'caption'    => get_post_field( 'post_excerpt', $attach_id ),
		'title'      => get_post_field( 'post_title', $attach_id ),
	);
}

/* ---------------------------------------------------------------
 * TranslatePress 字典表名稱解析（依 TRP 設定驗證目標語言合法性）
 * ------------------------------------------------------------- */
function synctify_tp_table( $language ) {
	global $wpdb;
	$settings = get_option( 'trp_settings' );
	if ( empty( $settings ) ) {
		return new WP_Error( 'no_trp', 'TranslatePress not configured', array( 'status' => 501 ) );
	}
	$default = $settings['default-language'];           // 例：en_US
	$targets = $settings['translation-languages'];      // 例：[en_US, zh_CN]
	if ( ! in_array( $language, $targets, true ) || $language === $default ) {
		return new WP_Error( 'bad_language', 'Invalid target language: ' . $language, array( 'status' => 400 ) );
	}
	// TRP 表名慣例：{prefix}trp_dictionary_{default}_{target}（小寫）
	return $wpdb->prefix . 'trp_dictionary_' . strtolower( $default ) . '_' . strtolower( $language );
}
