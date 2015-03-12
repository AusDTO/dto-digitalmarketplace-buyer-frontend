var gulp = require('gulp');
var uglify = require('gulp-uglifyjs');
var deleteFiles = require('del');
var sass = require('gulp-sass');
var filelog = require('gulp-filelog');

var environment;
var repoRoot = __dirname + '/';
var govukToolkitRoot = repoRoot + 'node_modules/govuk_frontend_toolkit';
var dmToolkitRoot = repoRoot + 'bower_components/digitalmarketplace_frontend_toolkit/toolkit';
var assetsFolder = repoRoot + 'app/assets';
var staticFolder = repoRoot + 'app/static';
var govukTemplateAssetsFolder = repoRoot + 'bower_components/govuk_template/assets';

// JavaScript paths
var jsVendorFiles = [
  assetsFolder + '/javascripts/vendor/jquery-1.11.0.js'
];
var jsSourceFiles = [
  dmToolkitRoot + '/javascripts/multi-selects.js',
  assetsFolder + '/javascripts/_onready.js'
];
var jsDistributionFolder = staticFolder + '/javascripts';
var jsDistributionFile = 'application.js';

// CSS paths
var cssSourceGlob = assetsFolder + '/scss/**/*.scss';
var cssDistributionFolder = staticFolder + '/stylesheets';

// Configuration
var sassOptions = {
  development: {
    outputStyle: 'expanded',
    lineNumbers: true,
    includePaths: [
      assetsFolder + '/scss',
      govukToolkitRoot + '/stylesheets',
      dmToolkitRoot + '/scss'
    ],
    sourceComments: true,
    errLogToConsole: true
  },
  production: {
    outputStyle: 'compressed',
    lineNumbers: true,
    includePaths: [
      assetsFolder + '/scss',
      govukToolkitRoot + '/stylesheets',
      dmToolkitRoot + '/scss'
    ],
  },
};

var uglifyOptions = {
  development: {
    mangle: false,
    output: {
      beautify: true,
      semicolons: true,
      comments: true,
      indent_level: 2
    }
  },
  production: {
    mangle: true
  }
};

gulp.task('clean', function () {
  var logOutputFor = function (fileType) {
    return function (err, paths) {
      console.log('Deleted the following ' + fileType + ' files:\n', paths.join('\n'));
    };
  };

  deleteFiles(jsDistributionFolder + '/*.js', logOutputFor('JavaScript'));
  deleteFiles(cssDistributionFolder + '/*.css', logOutputFor('CSS'));
});

gulp.task('sass', function () {
  var stream = gulp.src(cssSourceGlob)
    .pipe(filelog('Compressing SCSS files'))
    .pipe(sass(sassOptions[environment]))
    .on('error', function (err) {
      console.log(err.message);
    })
    .pipe(gulp.dest(cssDistributionFolder));

  stream.on('end', function () {
    console.log('Compressed CSS saved as .css files in ' + cssDistributionFolder)
  });

  return stream;
});

gulp.task('js', function () {
  // produce full array of JS files from vendor + local scripts
  jsFiles = jsVendorFiles.concat(jsSourceFiles);
  var stream = gulp.src(jsFiles)
    .pipe(filelog('Compressing JavaScript files'))
    .pipe(uglify(
      jsDistributionFile, 
      uglifyOptions[environment]
    ))
    .pipe(gulp.dest(jsDistributionFolder));

  stream.on('end', function () {
    console.log('Compressed JavaScript saved as ' + jsDistributionFolder + '/' + jsDistributionFile)
  });

  return stream;
});

gulp.task('copy_template_assets:stylesheets', function () {
  return gulp.src(govukTemplateAssetsFolder + '/stylesheets/**/*', { base : govukTemplateAssetsFolder + '/stylesheets' })
    .pipe(gulp.dest(staticFolder + '/stylesheets'))
});

gulp.task('copy_template_assets:images', function () {
  return gulp.src(govukTemplateAssetsFolder + '/images/**/*', { base : govukTemplateAssetsFolder + '/images' })
    .pipe(gulp.dest(staticFolder + '/images'))
});

gulp.task('copy_template_assets:javascripts', function () {
  return gulp.src(govukTemplateAssetsFolder + '/javascripts/**/*', { base : govukTemplateAssetsFolder + '/javascripts' })
    .pipe(gulp.dest(staticFolder + '/javascripts'))
});

gulp.task('copy_dm_toolkit_assets:images', function () {
  return gulp.src(dmToolkitRoot + '/images/**/*', { base : dmToolkitRoot + '/images' })
    .pipe(gulp.dest(staticFolder + '/images'))
});

gulp.task('copy_template_assets', function () {
   gulp.start('copy_template_assets:stylesheets');
   gulp.start('copy_template_assets:images');
   gulp.start('copy_template_assets:javascripts');
});

gulp.task('copy_dm_toolkit_assets', function () {
  gulp.start('copy_dm_toolkit_assets:images');
});

gulp.task('watch', ['build:development'], function () {
  var jsWatcher = gulp.watch([ assetsFolder + '/**/*.js' ], ['js']);
  var cssWatcher = gulp.watch([ assetsFolder + '/**/*.scss' ], ['sass']);
  var notice = function (event) {
    console.log('File ' + event.path + ' was ' + event.type + ' running tasks...');
  }

  cssWatcher.on('change', notice); 
  jsWatcher.on('change', notice); 
});

gulp.task('build:development', ['clean'], function () {
  environment = 'development';
  gulp.start('sass', 'js');
  gulp.start('copy_template_assets');
  gulp.start('copy_dm_toolkit_assets');
});

gulp.task('build:production', ['clean'], function () {
  environment = 'production';
  gulp.start('sass', 'js');
  gulp.start('copy_template_assets');
  gulp.start('copy_dm_toolkit_assets');
});